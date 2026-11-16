FROM python:3.12-slim

WORKDIR /srv

COPY app/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY contracts /srv/contracts
COPY data /srv/data
COPY sql /srv/sql
COPY app /srv/app

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
