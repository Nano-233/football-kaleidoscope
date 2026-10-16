FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY analytics analytics
COPY matchsim matchsim
COPY reference reference
COPY newsroom newsroom
COPY server server
COPY web web
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "server.app"]
