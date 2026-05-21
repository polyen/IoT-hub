FROM eclipse-mosquitto:2.0
# su-exec: drop to mosquitto user after fixing volume ownership as root
RUN apk add --no-cache openssl bash su-exec
COPY hub/mosquitto/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
# Override base-image USER mosquitto so entrypoint can chown named volumes
USER root
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["/usr/sbin/mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
