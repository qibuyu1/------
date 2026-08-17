# V22 Release Notes

## Editorial output

- Treat the supplied weekly report only as a writing-quality reference, not as a page/template contract.
- Remove weekly branding, fixed section numbering, forced observation callouts and weekly metadata from preview, WeChat HTML, DOCX and PDF.
- Add generic-title-prefix cleanup so the site scope “数据要素治理” is not mechanically prepended to every headline.
- Add true default choices for title, structure, opening, paragraph rhythm, evidence style, closing and generation quality.
- Turn inline source-number display off by default. Reference sections are emitted only when citations are explicitly enabled.

## JSON leakage

- Recover valid JSON returned in the plain-text DeepSeek salvage path.
- Recover `json { ... }` embedded inside `markdown` before anything is rendered.
- Add regression coverage that literal JSON keys and escaped `\\n` sequences cannot leak into visible article content.
- Add normal `.html` file export using the rendered article HTML.

## Emoticons

- Restore the original UI font inheritance for the article empty-state emoticon by removing the V21 preview-wide Songti override.
- Restore emoticons to search initial / empty / stopped / failure states.

## Serper images

- Fix the V21 duplicate precise-query fallback that spent another image search without broadening recall.
- Search source-report images first when available, then event/scene, then application/real-world imagery.
- Probe more ranked candidates so a couple of blocked original URLs do not make the whole image slot unresolved.
- Accept matching original-source hosts as a strong semantic anchor and use a more tolerant threshold for the single broad fallback query.

## Export

- Revert Word/PDF to a clean article layout: black Chinese display font for titles/headings, Songti body, natural headings, embedded images/captions.
- Do not append a bibliography by default.
