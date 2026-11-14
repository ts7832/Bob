# DrawQA UI

The judge-facing page: oliraff's drawing view (`viz/`, PR #5) on the left, and on the right the drawing picker,
the question box and the answer panel.

```bash
python tools/ui_server.py                       # -> http://localhost:8770   (classic demo page: /classic)
python tools/ui_server.py --viz <drawview.js>   # until PR #5 is merged: point at a built bundle
```

`tools/ui_server.py` loads `demo/server.py` as a module and subclasses its handler, so every `/api/*` route behaves
exactly as in the demo and nothing in `demo/` is edited. It adds `/` (this page), `/classic` (the original page),
`/viz/drawview.js` and `/api/gold` (the checked gold questions grouped by drawing).

## What the page does

- **Drawing.** Three tabs:
  - **Checked set:** the drawings in `gold/gold.jsonl` with verified or exact questions. Lines marked `generic` open as an unknown file, the way a judge's file arrives.
  - **Samples:** the demo's samples.
  - **Your file:** drop, pick or paste a path.
- **Question.** Type one, or click a rehearsed question. With "show checked answers" on, each shows its checked answer, or the dataset label for samples.
- **Answer.** Shows:
  - the value and the honesty tier (read, measured, detected, estimated, cannot determine)
  - how it was obtained, the explanation, the method, and what read the question (pattern or language model)
  - the time it took
  - for checked questions, whether it matches the checked answer (counts exact, areas and lengths within 2%, units converted)
- **Evidence.** Every item the answer used. Clicking one selects it in the drawing view, which zooms to it and shows the crop from the original. Clicking in the drawing marks the item in the list.
- **History.** The questions asked on the current drawing. Clicking one shows its answer again without asking again.
- Errors are shown where they happen: the server's message, a missing drawing view, or the page-1-only limit on multi-page sets.
