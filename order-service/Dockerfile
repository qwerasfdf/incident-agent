FROM eclipse-temurin:17-jdk AS build
WORKDIR /app
COPY gradlew .
COPY gradle gradle
COPY build.gradle settings.gradle ./
RUN chmod +x gradlew
# 의존성만 먼저 받아서 레이어 캐시 (소스만 바뀌면 이 레이어는 재사용됨)
RUN ./gradlew --no-daemon dependencies > /dev/null 2>&1 || true
COPY src src
RUN ./gradlew --no-daemon bootJar -x test

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/build/libs/*.jar app.jar
# 메모리 누수(S1) 장애가 빨리 드러나도록 힙을 256MB로 제한 (build.gradle의 bootRun과 동일한 설정)
ENV JAVA_TOOL_OPTIONS="-Xmx256m -Dfile.encoding=UTF-8"
EXPOSE 8081
ENTRYPOINT ["java", "-jar", "app.jar"]
