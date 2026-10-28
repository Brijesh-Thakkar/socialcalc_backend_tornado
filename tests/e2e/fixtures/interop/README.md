Real files used by `interop-fastapi.spec.ts` and `tests/interop/m1-curl.sh` (no synthetic payloads).

- `invoice.xls` (BIFF8) and `invoice.xlsx`: generated with LibreOffice headless from one invoice workbook:
  3 sheets (Invoice, Payments, Summary), 25 formulas (SUM, IF, SUMPRODUCT, cross-sheet), currency
  (`"₹"#,##0.00`) and date formats, merged cells (A1:F1, B3:C3, A16:F16, A1:C1), Hindi and ₹ text.
- `customers.csv`: 250 rows, quoted commas, embedded quotes and newlines, ₹, Devanagari, Latin-extended, CJK, empty cells.
- `table.html`: `<table>` with a header row, numbers, a `colspan="3"` total row and Hindi text.
- `invoice_rendered.html`: the LibreOffice HTML export of `invoice.xlsx` (input for HTML/PDF export).
- `native_BusinessInvoices.sc`: a real 10-sheet SocialCalc save string (`webappTemplates/BusinessInvoices.msc.txt`).

`SHA256SUMS` pins the bytes; the suite fails if a fixture changes.
