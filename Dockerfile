FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY itb ./itb
ENV PYTHONUNBUFFERED=1 ITB_DATA_DIR=/app/data ITB_SECRETS_DIR=/app/secrets
ENTRYPOINT ["python", "-m", "itb"]
# no arguments = a 10-minute dry-run: real schedule, real windows, probes built and signed, nothing sent
CMD ["run", "--minutes", "10", "--reps", "1"]
