FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PA_SKILLS_DIR=/app/skills

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# The assistant's skills: domain knowledge per app, loaded at runtime.
COPY skills ./skills

RUN pip install --no-cache-dir .

EXPOSE 8000 7860

CMD ["uvicorn", "pa.app:app", "--host", "0.0.0.0", "--port", "8000"]
