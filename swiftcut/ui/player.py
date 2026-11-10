"""Playback controller: wraps QMediaPlayer with frame-accurate seeking and selection-bounded play.

Qt reports positions in whole milliseconds, which is too coarse to identify frames reliably, so
the "current time" is taken from the start timestamp of the frame actually displayed. Qt shows
the last frame whose start is strictly before the requested position, hence `_seek_ms` targets
1 ms after a frame's start.
"""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget

from .trim_model import TrimModel

log = logging.getLogger(__name__)


class PlayerController(QObject):
    timeChanged = Signal(float)       # displayed frame time (s)
    playingChanged = Signal(bool)
    mediaReady = Signal()
    mediaFailed = Signal(str)

    def __init__(self, video: QVideoWidget, model: TrimModel, parent: QObject | None = None):
        super().__init__(parent)
        self.model = model
        self.loop = False
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(video)
        self._sink = video.videoSink()
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._time = 0.0
        self._offset = 0.0          # Qt frame time minus file-relative time
        self._calibrated = False
        self._ready = False
        self._pending_seek: float | None = None
        self._seek_in_flight = False
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(40)
        self._seek_timer.timeout.connect(self._flush_seek)
        self._path: str | None = None
        # Watchdog: if a seek produces no picture (a decoder hiccup can leave the view black),
        # ask again, at most twice per seek.
        self._last_seek_ms = 0
        self._seek_retries = 0
        self._target: float | None = None
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(1200)
        self._watchdog.timeout.connect(self._seek_stalled)
        # Fallback if no first frame arrives (e.g. a video Qt can't decode): carry on uncalibrated.
        self._calibration_timer = QTimer(self)
        self._calibration_timer.setSingleShot(True)
        self._calibration_timer.setInterval(2000)
        self._calibration_timer.timeout.connect(self._calibration_fallback)
        # Output device: None = follow the Windows default (also when it changes).
        self._device_id: bytes | None = None
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self._apply_device)
        # What you hear = user's preview switch AND what the export will contain. Qt plays one
        # audio track at a time, so we preview the first track that will be saved.
        self._user_muted = False
        self._track: int | None = 0          # position among audio tracks; None = silent
        self._track_switch_pending = False
        self._player.tracksChanged.connect(self._apply_audio)

    # -- source -----------------------------------------------------------------------------
    def open(self, path: str) -> None:
        self._target = None
        self._ready = False
        self._calibrated = False
        self._offset = 0.0
        self._time = 0.0
        self._path = path
        self._player.setSource(QUrl.fromLocalFile(path))

    def release(self) -> None:
        """Close the file so it can be replaced on disk."""
        # Clear state first: stop() emits status changes synchronously, and the status handler
        # must not act on them (calling back into the player mid-stop deadlocks Qt).
        self._path = None
        self._ready = False
        self._calibration_timer.stop()
        self._watchdog.stop()
        self._player.stop()
        self._player.setSource(QUrl())

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def time(self) -> float:
        return self._time

    @property
    def playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # -- audio ------------------------------------------------------------------------------
    @staticmethod
    def output_devices() -> list[QAudioDevice]:
        return list(QMediaDevices.audioOutputs())

    def device_id(self) -> bytes | None:
        return self._device_id

    def set_device(self, device_id: bytes | None) -> None:
        """Choose an output device by id, or None to follow the system default."""
        self._device_id = bytes(device_id) if device_id else None
        self._apply_device()

    def current_device(self) -> QAudioDevice:
        return self._audio.device()

    def _apply_device(self) -> None:
        target = QMediaDevices.defaultAudioOutput()
        if self._device_id is not None:
            for d in QMediaDevices.audioOutputs():
                if bytes(d.id()) == self._device_id:
                    target = d
                    break
            # If the chosen device is unplugged we fall back to the default, but keep the
            # preference so it's used again when it reappears.
        if bytes(self._audio.device().id()) != bytes(target.id()):
            log.info("audio output -> %s", target.description())
            self._audio.setDevice(target)

    def set_muted(self, muted: bool) -> None:
        """The user's preview-audio switch."""
        self._user_muted = muted
        self._apply_audio()

    def is_muted(self) -> bool:
        return self._user_muted

    def set_preview_track(self, position: int | None) -> None:
        """Play audio track number `position` (0-based), or nothing when None."""
        self._track = position
        self._apply_audio()

    def audible(self) -> bool:
        return not self._audio.isMuted()

    def active_track(self) -> int:
        return self._player.activeAudioTrack()

    def _apply_audio(self) -> None:
        self._audio.setMuted(self._user_muted or self._track is None)
        # Switching tracks is done on a clean event-loop pass (never nested inside another
        # signal handler) and merged when toggles come quickly: calling into Qt's player while
        # it's busy could otherwise lock up.
        if not self._track_switch_pending:
            self._track_switch_pending = True
            QTimer.singleShot(0, self._switch_track)

    def _switch_track(self) -> None:
        self._track_switch_pending = False
        if self._path is None or self._track is None:
            return
        if self._track < len(self._player.audioTracks()) and self._player.activeAudioTrack() != self._track:
            # Qt's video thread delivers frames to us while it switches tracks; with that delivery
            # connected, the switch can deadlock (Qt waits on its thread, which waits on Python).
            # Unhook frame delivery for the duration of the switch.
            self._sink.videoFrameChanged.disconnect(self._on_frame)
            try:
                self._player.setActiveAudioTrack(self._track)
            finally:
                self._sink.videoFrameChanged.connect(self._on_frame)

    def set_volume(self, v: float) -> None:
        self._audio.setVolume(max(0.0, min(1.0, v)))

    def volume(self) -> float:
        return self._audio.volume()

    # -- transport --------------------------------------------------------------------------
    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if not self._ready or not self.model.loaded:
            return
        self._target = None                    # playing: every frame counts
        m = self.model
        last_start = m.grid.times[m.end_idx]
        # Restart from the selection start if we're outside it or parked on its last frame.
        if self._time < m.start_time - 1e-4 or self._time >= last_start - 1e-4:
            self.seek(m.start_time, immediate=True)
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def seek(self, t: float, immediate: bool = False) -> None:
        """Show the frame at time t. Rapid calls (scrubbing) are coalesced."""
        if not self._ready:
            return
        self._pending_seek = t
        if immediate or not self._seek_in_flight:
            self._flush_seek()
        else:
            # Deferred, but report the target position now, so actions that read the playhead
            # straight away (mark in/out, frame steps) use where the user asked to be.
            g = self.model.grid
            self._set_time(g.times[g.index_at(t)] if g is not None else t)
            if not self._seek_timer.isActive():
                self._seek_timer.start()

    def seek_frame(self, index: int) -> None:
        g = self.model.grid
        if g is None:
            return
        index = max(0, min(g.last_index, index))
        # Coalesced: the first request goes straight out, rapid repeats (held arrow keys or
        # buttons) merge, so the decoder is never flooded with seeks.
        self.seek(g.times[index])

    def step(self, frames: int) -> None:
        g = self.model.grid
        if g is None or not self._ready:
            return
        self.pause()
        self.seek_frame(g.index_at(self._time) + frames)

    def current_index(self) -> int:
        g = self.model.grid
        return g.index_at(self._time) if g else 0

    def _flush_seek(self) -> None:
        if self._pending_seek is None:
            return
        t = self._pending_seek
        self._pending_seek = None
        self._seek_in_flight = True
        g = self.model.grid
        snapped = g.times[g.index_at(t)] if g is not None else t
        self._target = snapped                 # until this frame arrives, older frames are stale
        self._last_seek_ms = self._seek_ms(t)
        self._seek_retries = 0
        self._player.setPosition(self._last_seek_ms)
        self._watchdog.start()
        # Reflect the target immediately so the UI feels instant.
        self._set_time(snapped)
        self._seek_timer.start()

    def _seek_ms(self, t: float) -> int:
        g = self.model.grid
        if g is not None:
            t = g.times[g.index_at(t)]
        t += self._offset
        return 0 if t <= 0.0005 else int(math.floor(t * 1000 + 1e-6)) + 1

    # -- Qt callbacks -----------------------------------------------------------------------
    def _on_frame(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        qt_t = frame.startTime() / 1e6
        if not self._calibrated:
            # The first frame shown after loading is frame 0 (time 0 in our timeline). Whatever
            # Qt calls it is a constant offset between Qt's clock and ours.
            self._offset = qt_t
            self._calibrated = True
            self._calibration_timer.stop()
            self._become_ready()
        elif not self._ready:
            return
        t = qt_t - self._offset
        if not self.playing and self._pending_seek is not None:
            # A newer seek is queued: this frame is already out of date. Send the queued seek
            # now (the decoder is free again) rather than showing this position.
            self._seek_in_flight = False
            self._watchdog.stop()
            self._flush_seek()
            return
        if not self.playing and self._target is not None:
            # While paused, only the frame we asked for most recently counts. Frames from earlier,
            # superseded seeks can still arrive afterwards and must not move the playhead back.
            g = self.model.grid
            tol = (g.min_interval / 2) if g is not None else 0.005
            if abs(t - self._target) > max(tol, 0.0015):
                return
            # Keep the target until the next seek: late frames from older seeks stay ignored.
        self._seek_in_flight = False
        self._watchdog.stop()
        self._set_time(t)
        if self.playing and self.model.loaded:
            if t >= self.model.end_time - 1e-4:
                self._reached_end()

    def _reached_end(self) -> None:
        if self.loop:
            self.seek(self.model.start_time, immediate=True)
        else:
            self._player.pause()
            g = self.model.grid
            self.seek(g.times[self.model.end_idx], immediate=True)

    def _begin_calibration(self) -> None:
        if self._path is not None and not self._ready:
            self._player.pause()   # paused state renders the first frame, which calibrates us

    def _seek_stalled(self) -> None:
        if not self._ready or self.playing or self._path is None:
            return
        frame = self._sink.videoFrame()
        shown = frame.isValid() and abs(frame.startTime() / 1000 - self._last_seek_ms) < 40
        if shown:
            return
        if self._seek_retries >= 2:
            self._target = None                # give up waiting; accept whatever is shown next
            self._seek_in_flight = False
            return
        self._seek_retries += 1
        log.warning("seek to %d ms produced no frame; retrying (%d)", self._last_seek_ms, self._seek_retries)
        self._player.pause()
        self._player.setPosition(self._last_seek_ms)
        self._watchdog.start()

    def _become_ready(self) -> None:
        if not self._ready:
            self._ready = True
            self.mediaReady.emit()

    def _calibration_fallback(self) -> None:
        if not self._calibrated and self._path:
            log.warning("no first frame within 2 s; preview timing uncalibrated")
            self._calibrated = True
            self._offset = 0.0
            self._become_ready()

    def _set_time(self, t: float) -> None:
        self._time = max(0.0, t)
        self.timeChanged.emit(self._time)

    def _on_status(self, status: QMediaPlayer.MediaStatus) -> None:
        S = QMediaPlayer.MediaStatus
        if self._path is None:
            return   # released, or mid-release: ignore the player's shutdown chatter
        if status == S.LoadedMedia and not self._ready and not self._calibration_timer.isActive():
            # Never call back into the player from inside its own signal; do it next tick.
            self._calibration_timer.start()
            QTimer.singleShot(0, self._begin_calibration)
        elif status == S.EndOfMedia:
            if self.loop and self.model.loaded:
                self.seek(self.model.start_time, immediate=True)
                self._player.play()
            else:
                self._player.pause()
                if self.model.loaded:
                    self.seek(self.model.grid.times[self.model.end_idx], immediate=True)
        elif status == S.InvalidMedia:
            self.mediaFailed.emit(self._player.errorString() or "This file can't be played.")

    def _on_state(self, _state) -> None:
        self.playingChanged.emit(self.playing)

    def _on_error(self, _err, message: str) -> None:
        log.warning("player error: %s", message)
        if not self._ready:
            self.mediaFailed.emit(message or "This file can't be played.")
