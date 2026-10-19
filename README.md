# cc-video-studio

A skill for producing code-based corporate, product, and launch videos with agents, alongside [Cinetic](https://github.com/Leonxlnx/cinetic), which directs the visuals. This skill adds:

- a sound circuit: music selected and edited to bars, effects recorded by event, and multiband mixing;
- voice by takes, checked for transcription and tone;
- image and audio checks;
- [earshot](https://github.com/Leonxlnx/earshot) as an optional dependency pinned to a commit: listening to the mix before delivery and placing effects from the bank on the right frame;
- lessons from real productions.

When the two skills differ, Cinetic takes precedence, except for sound, voice, and content, where this skill states what it replaces.

## Install in a video project

Install at the project level, never globally, with the `skills` CLI, which records it in the project's `skills-lock.json`:

```sh
npm_config_cache=$PWD/.npm-cache npx skills add FacuVCanale/cc-video-studio --skill video-studio -a codex claude-code -y
npm_config_cache=$PWD/.npm-cache npx skills add Leonxlnx/cinetic --skill cinetic -a codex claude-code -y
```

earshot is optional. It runs from the pinned commit through `uv`/`uvx`, or through the virtual environment selected by `EARSHOT_PYTHON`; without either, the rest of the skill still works.

To update, run `npx skills update -p -y`.

## Contents

- `skills/video-studio/SKILL.md`: what it adds and where it fits into Cinetic's workflow.
- `skills/video-studio/references/`: lessons, sound, voice, product and data, and verification.
- `skills/video-studio/scripts/`: engine-independent audio and verification tools that read Cinetic's cues and media, plus the Remotion-specific `determinism.sh`, `drop_bundle_on_exit.mjs`, and `clean_render_tmp.sh` helpers.
- `tests/`: script tests with synthetic audio and video.

Third-party notices for Cinetic and earshot are in [THIRD_PARTY.md](THIRD_PARTY.md).
