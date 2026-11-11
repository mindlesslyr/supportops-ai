FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --system supportops && useradd --system --gid supportops --home /app supportops
COPY --chown=supportops:supportops app ./app
RUN mkdir -p /app/data && chown -R supportops:supportops /app
USER supportops
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
