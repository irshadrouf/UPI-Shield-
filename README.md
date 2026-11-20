# UPI Shield — Existing Project as One Web App

This keeps the existing UPI Shield dashboard and analysis backend together. The existing design and features are preserved; the home route now opens the dashboard instead of the raw API status JSON.

## Run on Windows
1. Extract this ZIP.
2. Open the extracted `UPI_Shield_Final` folder in VS Code.
3. Open a VS Code terminal in this folder.
4. Run:

```powershell
python -m venv .venv
.venv\\Scripts\\activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

5. Open **http://127.0.0.1:8000** in Chrome.

Use this single address. Do not use Live Server on port 5500 for this setup. Keep the terminal running while using the website.

## OCR requirement
Tesseract OCR must be installed for screenshot text extraction. `main.py` already checks the common Windows installation path:
`C:\\Program Files\\Tesseract-OCR\\tesseract.exe`

If it is installed elsewhere, update `TESSERACT_PATH` near the top of `main.py`.

## Existing features
- Existing dark UPI Shield dashboard
- Multiple screenshot selection
- OCR and risk indicator analysis
- Duplicate transaction/image checks
- Metadata and ELA indicators
- Browser-local scan history

A low-risk result is not proof that a payment was transferred. Always verify in the bank or UPI app.
