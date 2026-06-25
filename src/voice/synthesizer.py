import re
from pathlib import Path
from pydub import AudioSegment as PydubAudio
from rich.console import Console
from ..analysis.models import ScriptSegment
from .models import AudioSegment

console = Console()

# v3 audio tags that the model interprets for emotion/delivery/SFX.
# These must NOT be stripped from the text — they're meaningful to the model.
_V3_TAG_PATTERN = re.compile(
    r'\[('
    # Emotions
    r'excited|nervous|frustrated|tired|sad|angry|happily|calm|'
    r'sorrowful|annoyed|flustered|casual|awe|wistful|exhausted|'
    # Tone & delivery
    r'whispers?|whispering|shouts?|shouting|quietly|loudly|'
    r'cheerfully|flatly|deadpan|playfully|sarcastically|sarcastic tone|'
    r'matter-of-fact|dramatic|dramatic tone|lighthearted|reflective|'
    r'serious tone|conversational tone|whiny|timidly|understated|'
    # Pacing
    r'pause|pauses|rushed|slows down|deliberate|rapid-fire|fast-paced|'
    r'drawn out|continues after a beat|continues softly|hesitates|stammers|'
    # Human sounds
    r'laughs?|laughing|nervous laugh|laughs harder|starts laughing|'
    r'soft chuckle|wheezing|giggles?|sighs?|gasps?|gulps?|'
    r'clears throat|crying|sobbing|groans?|breathes?|breathes deeply|'
    r'sniffles|coughs?|yawns?|inhales deeply|'
    # Emphasis
    r'emphasized|stress on next word|'
    # Sound effects
    r'clapping|applause|WHISPER|SHOUTING|'
    # Community-tested
    r'mutters|mumbles|murmurs|screaming|yells|snickers|chuckles|snorts|'
    r'trembling voice|shaky|voice breaking|voice cracking|through tears|'
    r'under breath|trailing off|growing louder|speaking quickly|speaking slowly'
    r')\]',
    re.IGNORECASE,
)


class VoiceSynthesizer:
    """ElevenLabs-based voice synthesizer with v3 audio tag support."""

    def __init__(
        self,
        api_key: str,
        voice_id: str | None = None,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        model_id: str = "eleven_v3",
        use_speaker_boost: bool = True,
    ):
        from elevenlabs.client import ElevenLabs
        self.client = ElevenLabs(api_key=api_key)
        self.voice_id = voice_id
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.style = style
        self.model_id = model_id
        self.use_speaker_boost = use_speaker_boost

    def _clean_text(self, text: str) -> str:
        """Clean narration text. Preserves v3 audio tags, strips stage directions."""
        if "v3" in self.model_id:
            # v3: keep recognized audio tags, strip everything else in brackets
            def _keep_or_strip(match: re.Match) -> str:
                if _V3_TAG_PATTERN.match(match.group(0)):
                    return match.group(0)
                return ''
            return re.sub(r'\[.*?\]', _keep_or_strip, text).strip()
        else:
            # v2 and older: strip all bracketed tags (model can't use them)
            text = re.sub(r'\[PAUSE\]', '...', text, flags=re.IGNORECASE)
            return re.sub(r'\[.*?\]', '', text).strip()

    def clone_voice(self, name: str, samples_dir: Path) -> str:
        """Clone a voice from audio samples. Returns the voice_id."""
        sample_files = list(samples_dir.glob("*.mp3")) + list(samples_dir.glob("*.wav"))
        if not sample_files:
            raise FileNotFoundError(f"No audio samples found in {samples_dir}")

        console.print(f"[blue]Cloning voice from {len(sample_files)} samples...[/blue]")

        file_contents = []
        for f in sample_files[:25]:
            file_contents.append(open(f, "rb"))

        try:
            voice = self.client.voices.ivc.create_voice_from_preview(
                voice_name=name,
                audio_files=file_contents,
            )
        except Exception:
            voice = self.client.clone(
                name=name,
                files=[str(f) for f in sample_files[:25]],
            )
        finally:
            for f in file_contents:
                f.close()

        self.voice_id = voice.voice_id
        console.print(f"[green]Voice cloned: {voice.voice_id}[/green]")
        return voice.voice_id

    def synthesize_segment(
        self,
        segment: ScriptSegment,
        output_dir: Path,
    ) -> AudioSegment:
        """Generate speech for a single script segment."""
        if not self.voice_id:
            raise ValueError("No voice_id set. Clone a voice first or set voice_id.")

        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"audio_{segment.segment_id}.mp3"

        clean_text = self._clean_text(segment.narration_text)
        console.print(f"[blue]Synthesizing: {segment.segment_id} ({len(clean_text)} chars, model={self.model_id})[/blue]")

        convert_kwargs = dict(
            text=clean_text,
            voice_id=self.voice_id,
            model_id=self.model_id,
            output_format="mp3_44100_128",
            voice_settings={
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
                "style": self.style,
                "use_speaker_boost": self.use_speaker_boost,
            },
        )
        audio_generator = self.client.text_to_speech.convert(**convert_kwargs)

        with open(audio_path, "wb") as f:
            for chunk in audio_generator:
                f.write(chunk)

        audio = PydubAudio.from_mp3(str(audio_path))
        duration = len(audio) / 1000.0

        console.print(f"[green]Audio: {segment.segment_id} = {duration:.1f}s[/green]")

        return AudioSegment(
            segment_id=segment.segment_id,
            audio_path=audio_path,
            duration_seconds=duration,
            narration_text=segment.narration_text,
        )

    def synthesize_all(
        self,
        segments: list[ScriptSegment],
        output_dir: Path,
    ) -> list[AudioSegment]:
        """Synthesize audio for all segments sequentially."""
        results = []
        for segment in segments:
            result = self.synthesize_segment(segment, output_dir)
            results.append(result)
        return results
