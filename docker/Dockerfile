FROM python:3.12-slim
WORKDIR /app
COPY apps/api /app
RUN pip install --no-cache-dir ".[postgres]"
EXPOSE 7420
CMD ["uvicorn", "tynode_api.main:app", "--host", "0.0.0.0", "--port", "7420"]