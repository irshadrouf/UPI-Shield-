# UPI Shield — Render deployment package

The existing UPI Shield frontend and FastAPI backend are kept together. The only deployment additions are `Dockerfile` and `render.yaml`; the Docker image installs Tesseract OCR for hosted screenshot analysis.

## Deploy
1. Create a GitHub repository and upload the contents of this folder (the files inside `UPI_Shield_Render_Deploy`, not an extra enclosing folder).
2. Sign in to Render: https://dashboard.render.com/
3. Choose **New + → Blueprint** and connect the GitHub repository containing `render.yaml`.
   - Or choose **New + → Web Service**, connect the repository, and select Docker runtime.
4. Render builds the Dockerfile and deploys the service.
5. Open the `onrender.com` URL shown by Render. The dashboard should load at the root URL.

## Important deployment notes
- The app listens on Render's assigned `PORT` and binds to `0.0.0.0`.
- Tesseract OCR is installed inside the Docker image.
- Render free web services can spin down after inactivity and may take time to wake.
- The free service filesystem is ephemeral. SQLite data such as duplicate-scan history may reset when the service restarts or redeploys. Persistent storage requires a suitable persistent disk/paid setup or an external database.
- A low-risk screenshot result does not prove that a payment was transferred. Verify with the bank/UPI app.
