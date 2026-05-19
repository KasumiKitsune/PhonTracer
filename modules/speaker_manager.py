import uuid

class SpeakerState:
    def __init__(self, name):
        self.id = str(uuid.uuid4())
        self.name = name
        self.items = {}
        self.audio_cache = {}
        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'skip_front': 0.00,
            'pitch_floor': 75,
            'pitch_ceiling': 600,
            'voicing_threshold': 0.25
        }
        self.pending_long_snd = None
        self.pending_batch_paths = []
        self.current_macro_segments = []
        self.manual_segments = None
        self.tab_mode = "多条独立音频"

class SpeakerManager:
    def __init__(self):
        default_speaker = SpeakerState("发音人 1")
        self.speakers = {default_speaker.id: default_speaker}
        self.active_speaker_id = default_speaker.id

    def get_active_speaker(self):
        return self.speakers[self.active_speaker_id]

    def add_speaker(self, name):
        new_speaker = SpeakerState(name)
        self.speakers[new_speaker.id] = new_speaker
        return new_speaker

    def remove_speaker(self, speaker_id):
        if speaker_id in self.speakers and len(self.speakers) > 1:
            del self.speakers[speaker_id]
            if self.active_speaker_id == speaker_id:
                self.active_speaker_id = list(self.speakers.keys())[0]
            return True
        return False

    def rename_speaker(self, speaker_id, new_name):
        if speaker_id in self.speakers:
            self.speakers[speaker_id].name = new_name
            return True
        return False

    def set_active_speaker(self, speaker_id):
        if speaker_id in self.speakers:
            self.active_speaker_id = speaker_id
            return True
        return False

    def get_all_speakers(self):
        return list(self.speakers.values())
