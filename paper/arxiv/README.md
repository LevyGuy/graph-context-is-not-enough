# arXiv Submission Package

This folder contains a LaTeX version of the current paper draft.

Files:

- `main.tex`: main manuscript
- `references.bib`: bibliography

Before submission, replace:

- author name formatting and affiliation placeholders in `main.tex`
- acknowledgments and disclosure text
- any wording you want to tighten for final submission

Recommended additions before final arXiv upload:

1. compile the manuscript with a local TeX distribution or Overleaf
2. proofread tables and line breaks in the compiled PDF
3. add a Zenodo DOI once the artifact bundle is archived
4. update the reproducibility section with the final artifact links

Suggested compile commands:

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

If you prefer `latexmk`:

```bash
latexmk -pdf main.tex
```
