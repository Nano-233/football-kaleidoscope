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
# gzip copies beside the page and its match data: aiohttp serves them to browsers that accept gzip (a match drops from 5 MB to 2 MB)
RUN python -c "import gzip, pathlib; [p.with_name(p.name + '.gz').write_bytes(gzip.compress(p.read_bytes(), 6)) for p in pathlib.Path('web').rglob('*') if p.is_file() and p.suffix in ('.json', '.bin', '.js', '.css', '.html', '.glb')]"
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "server.app"]
