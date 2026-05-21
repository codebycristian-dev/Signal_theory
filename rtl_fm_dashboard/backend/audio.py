import queue
import numpy as np


class AudioPlayer:
    """
    Reproductor de audio usando sounddevice.
    El callback nunca debe hacer DSP pesado: solo consume bloques listos.
    """

    def __init__(self, cfg, audio_queue: queue.Queue):
        import sounddevice as sd

        self.cfg = cfg
        self.audio_queue = audio_queue
        self.underflows = 0
        self.started = False

        self.stream = sd.OutputStream(
            samplerate=self.cfg.audio_rate,
            channels=1,
            dtype="float32",
            blocksize=self.cfg.audio_block_size,
            latency="high",
            callback=self.callback,
        )

    def callback(self, outdata, frames, time_info, status):
        try:
            audio = self.audio_queue.get_nowait()
        except queue.Empty:
            self.underflows += 1
            audio = np.zeros(frames, dtype=np.float32)

        if len(audio) < frames:
            temp = np.zeros(frames, dtype=np.float32)
            temp[: len(audio)] = audio
            audio = temp
        elif len(audio) > frames:
            audio = audio[:frames]

        outdata[:, 0] = audio

    def start(self):
        if not self.started:
            self.stream.start()
            self.started = True

    def stop(self):
        if self.started:
            self.stream.stop()
            self.started = False
        self.stream.close()
