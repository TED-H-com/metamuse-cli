# Project page

This directory is the GitHub Pages site for MetaMuse CLI. `page.json` is the source. The HTML, CSS, and script files next to it are generated. Change `page.json`, then render again. Do not hand-edit the generated files.

Renderer: `kit/render.py` from `teddashh/teddashh.github.io` at commit `5e51fb954acef56e9f75b4cfd2b57dc76afcdfd8`.

Keep the existing `teal` palette, typography, and shared kit components. User workflows belong in the top-level `flows` array, which renders the same animated SVG diagrams used by sibling project pages. Each diagram needs plain-language steps, bilingual labels, a readable mobile legend, and an accurate description of account setup or approval. Do not replace these with a second diagram library or a custom visual theme.

Set `PAGE_KIT_DIR` to a checkout of that `kit/` directory when rendering or running `scripts/check_site.py`.

License: MIT, Copyright (c) 2026 Ted Huang (`LICENSE` in `teddashh/teddashh.github.io` at commit `5e51fb954acef56e9f75b4cfd2b57dc76afcdfd8`). The generated `styles.css`, `lang.js`, `app.js`, `flow.js`, and `flow.css` are portions of that kit.

```
MIT License

Copyright (c) 2026 Ted Huang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Terminal frames and their plain-text transcripts live in `images/`. See `images/README.md` and `images/manifest.json`.
