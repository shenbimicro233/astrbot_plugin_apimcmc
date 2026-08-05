# Bundled Fonts

## Monocraft Font

This directory bundles the unmodified regular TrueType build of Monocraft.

- Source: <https://github.com/IdreesInc/Monocraft>
- Release: `v4.2.1`
- Commit: `04d8a6f3c0fcddf9ab0ec6c21aefcf6302392464`
- Upstream path: `dist/Monocraft-ttf/Monocraft.ttf`
- File SHA-256: `262e31822e0a75be567b02320bfa092143d871e8a273e529d1c5ab0c04cd215f`
- OFL normalized-text SHA-256: `f69c147003e052dbc9d96c40a9f73647e72766cfda95a597b94ed827fe25acb1`
- Copyright: Copyright (c) 2022, Idrees Hassan

The bundled font includes its Standard Galactic Alphabet glyphs at private-use
code points `U+EB40` through `U+EB59`. The renderer maps ASCII letters to this
range case-insensitively and leaves digits and punctuation unchanged.

Monocraft is distributed under the SIL Open Font License 1.1. The complete
copyright notice and license are preserved in `OFL-Monocraft.txt`. The font
remains under OFL-1.1 when bundled with this AGPL-licensed plugin. The public
license page is <https://openfontlicense.org/>.

## Fusion Pixel Font

This directory also bundles the regular TrueType build of Fusion Pixel 10px
Mono ja.

- Source: <https://github.com/TakWolf/fusion-pixel-font>
- Release: `2026.07.20`
- Release commit: `c29615e1f629c6bb27dac3e6dcaa4556e629d09f`
- Upstream asset: `fusion-pixel-font-10px-monospaced-ttf-v2026.07.20.zip`
- Asset ZIP SHA-256: `3d2719a7720d405167b494b7fa1c8e721bfbda59183cccc696ef5d43b0be5945`
  (ja variant)
- Local file: `fusion-pixel-10px-monospaced-ja.ttf`
- File SHA-256: `a476f9fc45dbd2b8b292f6264dfd0aa1a5e9e4ee464c819f306c5e0ee141b227`
- Copyright: Copyright (c) 2022, TakWolf (https://takwolf.com)
- License SHA-256 (normalized text): `bc518cf64b8032c07690f33cc270c35c179255a6ac8efa7c165ebae7e8f76a63`

The complete OFL-1.1 notice is preserved in `OFL-FusionPixel.txt`. The bundled
TTF and OFL text
were verified byte-identical to the official `2026.07.20` release assets
(commit `c29615e1f629c6bb27dac3e6dcaa4556e629d09f`). The public license page
is <https://openfontlicense.org/>.

In the default Chinese chart-text mode, all chart copy is Chinese and is
rendered only with this bundled font. Galactic mode uses bundled Monocraft;
system mode does not use either bundled font.
