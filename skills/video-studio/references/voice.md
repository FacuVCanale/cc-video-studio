# Voice-over

## Script
- The text comes from the source material (deck, the user's notes) and does not change. Refine the delivery: how it
  is written so the voice says it correctly (a separate `tts` field from the visible text).
- **Length:** in Spanish, around 13–15 characters per second. If a line does not fit its scene, adjust the shot or
  rewrite it with the user. Never speed up the voice.
- **Coverage:** the voice accompanies most of the film. A gap longer than 2.5 s with only UI feels empty. Leave room
  in the hook, climax, and ending.

## Story
- **The voice tells a story; it does not read the slides:** a hook, tension, turn, proof, and ending, from one point
  of view. Write it before the video, with an idea of what appears at each moment.
- **It makes sense outside the client's world.** Replace an internal operational detail with what the listener
  gains. The hook speaks about what the company does overall, not a single product.
- **Telling a story does not mean cutting substance.** Keep the core capabilities from the original material,
  expressed within the story. A list of services interrupts the story and should be removed.

## Writing for TTS
- **Multiline takes.** Generating isolated short lines causes repetition. Generate paragraphs of 2 to 4 lines and
  cut them at the real silence between one line and the next (`silence_cut.py`, used by `tts_takes.py`).
- **Never cut at the alignment midpoint.** Character or word timestamps from TTS can drift by up to half a second:
  the midpoint between the last word of one line and the first of the next may fall inside a word. Cut at the
  longest gap below the speech threshold measured by energy between those two words (with tolerance), at the
  quietest frame that leaves silence on both sides, with a short fade. If there is no gap, the script reports it
  and that line is regenerated or reviewed by ear.
- **Changing one line regenerates its entire segment** (the line and its neighbors) to keep the tone consistent. An
  isolated patch is noticeable.
- **Use a fixed voice and label for each voice** (`audio/voice/voices.json`). Changing the label changes the
  character: with a different one, a voice sounded as though it were whispering.
- **Rioplatense voseo:** models neutralize the imperative ("Probalo" → "Pruébalo") and the stress of some verbs
  ("vendés" → "vendes"). Rewrite it or select the seed that passes the check.
- **Initialisms, numbers, and units:** write them as they are spoken (`tCO₂e` → "toneladas de CO2 equivalente").
  An initialism beside a vowel may merge with it: put it at the end of a sentence or before a comma.
- **Punctuation:** colons and commas introduce pauses of 0.6–1 s. A line that must sound fast gets only one pause.

## Engines
- **Local and free:** Kokoro-82M (Apache-2.0), with word timestamps from faster-whisper. This is the starting point.
- **Paid:** ElevenLabs through fal, with character timestamps. Only with a plan, cost, and cap approved by the
  user.
- To choose a voice, the user listens to samples of several voices saying the same sentence.

## Checks (`voice_qc.py`)
- Local Whisper transcribes each line and compares it word by word with the script. It flags repetitions, missing
  words, and changes. Homophones use an equivalence table (`audio/voice/equivalences.json`). A line that fails is
  not presented.
- **Consistent tone:** measure the median F0 and p90 of each line against the approved lines. A lower, flatter
  segment sounds like another person, so regenerate the entire segment.
- **Edges (`voice_edges.py`):** the first and last 30 ms of each take stay below −45 dBFS RMS; use
  `--measure peak --window-ms 20 --threshold-dbfs -30` to catch a single click that RMS averages away. Silence
  trimming uses margins of 150 ms before and 250 ms after on uncompressed audio, with a 10 ms fade-in and 60 ms
  fade-out, and normalization comes last.

## Placement (`voice_mix.py`)
- Each line starts at its cue, without speeding up. If it does not fit, move the next line by at most 1 s. Two
  consecutive lines from the same take preserve their natural pause.
- Write the isolated voice, placement, and subtitles with every word on its frame: SRT, plus word markers for pills
  or burned-in subtitles.
- **Subtitles:** ≤ 42 characters, broken at punctuation and never after a linking word. Hide them when the screen
  already says the same thing in large type, and never cover a phrase or the UI focus.
