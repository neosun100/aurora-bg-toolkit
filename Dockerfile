FROM amazoncorretto:17-alpine AS build
WORKDIR /workspace

# Install maven (alpine doesn't bundle it)
RUN apk add --no-cache maven bash

# Copy sources in dependency-friendly order so layer caching works
COPY pom.xml ./
COPY lib/ ./lib/
COPY scripts/install-local-wrapper-jars.sh ./scripts/install-local-wrapper-jars.sh
RUN chmod +x scripts/install-local-wrapper-jars.sh && \
    ./scripts/install-local-wrapper-jars.sh

COPY src/ ./src/

# Default profile (4.0.0); override with --build-arg WRAPPER_PROFILE=wrapper-3.3
ARG WRAPPER_PROFILE=
RUN if [ -n "$WRAPPER_PROFILE" ]; then \
        mvn -q -B clean package -DskipITs -P${WRAPPER_PROFILE}; \
    else \
        mvn -q -B clean package -DskipITs; \
    fi

# ──────────────────────────────────────────────────────────────────────────
FROM amazoncorretto:17-alpine
RUN apk add --no-cache bash

WORKDIR /app
COPY --from=build /workspace/target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar ./aurora-bg-toolkit.jar
COPY configs/ /app/configs/

# Defaults documented; override at run time:
#   docker run -e DB_ENDPOINT=... -e DB_PASSWORD=... ... \
#       aurora-bg-toolkit /app/configs/v4-current.yaml
ENV DB_PORT=4488 \
    DB_NAME=demo \
    DB_USER=admin

ENTRYPOINT ["java", \
    "--add-opens", "java.base/java.lang=ALL-UNNAMED", \
    "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED", \
    "-jar", "/app/aurora-bg-toolkit.jar"]

CMD ["/app/configs/v4-current.yaml"]
