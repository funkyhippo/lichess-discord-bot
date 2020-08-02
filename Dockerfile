FROM python:3
RUN mkdir /bot
COPY requirements.txt /bot/requirements.txt
RUN  pip  install -r /bot/requirements.txt
WORKDIR /bot
