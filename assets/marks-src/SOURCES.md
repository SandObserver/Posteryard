# Mark sources

Each SVG comes from Wikimedia Commons, where it is marked public domain, or from [Simple Icons](https://simpleicons.org) 16.34.0, released under CC0 1.0. The marks are trademarks of their owners. Services without a file here get their TMDB network logo, or a cut of their TMDB provider icon, at runtime (`src/posteryard/automarks.py`). No other logo files are stored in the repository.

| File | Source | License on Commons |
| --- | --- | --- |
| `netflix.svg` | [Netflix 2015 logo.svg](https://commons.wikimedia.org/wiki/File:Netflix_2015_logo.svg) | Public domain |
| `prime.svg` | [Prime Video logo (2024).svg](https://commons.wikimedia.org/wiki/File:Prime_Video_logo_(2024).svg) | Public domain |
| `appletv.svg` | [Apple TV 4K (logo).svg](https://commons.wikimedia.org/wiki/File:Apple_TV_4K_(logo).svg) | Public domain |
| `disney.svg` | [Disney+ 2024.svg](https://commons.wikimedia.org/wiki/File:Disney%2B_2024.svg) | Public domain |
| `crave.svg` | [Crave 2018 logo.svg](https://commons.wikimedia.org/wiki/File:Crave_2018_logo.svg) | Public domain |
| `paramountplus.svg` | [Paramount+ logo.svg](https://commons.wikimedia.org/wiki/File:Paramount%2B_logo.svg) | Public domain |
| `hbomax.svg` | [HBO Max 2025.svg](https://commons.wikimedia.org/wiki/File:HBO_Max_2025.svg) | Public domain |
| `hulu.svg` | [Hulu logo (2018).svg](https://commons.wikimedia.org/wiki/File:Hulu_logo_(2018).svg) | Public domain |
| `peacock.svg` | [NBCUniversal Peacock Logo (2026).svg](https://commons.wikimedia.org/wiki/File:NBCUniversal_Peacock_Logo_(2026).svg) | Public domain |
| `youtube.svg` | [YouTube Logo 2017.svg](https://commons.wikimedia.org/wiki/File:YouTube_Logo_2017.svg) | Public domain |
| `plutotv.svg` | [Pluto TV logo 2020.svg](https://commons.wikimedia.org/wiki/File:Pluto_TV_logo_2020.svg) | Public domain |
| `crunchyroll.svg`, `tubi.svg`, `starz.svg`, `mubi.svg`, `viaplay.svg`, `sky.svg`, `now.svg`, `rtl.svg`, `movistar.svg`, `channel4.svg` | [Simple Icons](https://github.com/simple-icons/simple-icons) 16.34.0, same slugs | CC0 1.0 |
| `dolbyvision.svg` | [Dolby Vision (logo).svg](https://commons.wikimedia.org/wiki/File:Dolby_Vision_(logo).svg) | Public domain |
| `dolbyatmos.svg` | [Dolby Atmos (logo).svg](https://commons.wikimedia.org/wiki/File:Dolby_Atmos_(logo).svg) | Public domain |

`appletv.svg` has the "4K" glyphs removed.

Rebuild the PNG marks after a change: `uv run python tools/build_marks.py`.
