package com.aurora.bgtest.config;

import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * Loads {@link TestConfig} from a YAML file or stream.
 *
 * <p>Uses snakeyaml's safe constructor — no class instantiation from YAML tags,
 * so loading untrusted files cannot lead to arbitrary code execution.
 */
public final class ConfigLoader {

    private ConfigLoader() {}

    public static TestConfig fromPath(Path path) throws IOException {
        try (InputStream in = Files.newInputStream(path)) {
            return fromStream(in, path.toString());
        }
    }

    public static TestConfig fromString(String yaml) {
        return parse(newYaml().load(yaml), "<string>");
    }

    public static TestConfig fromStream(InputStream in, String source) {
        return parse(newYaml().load(in), source);
    }

    private static Yaml newYaml() {
        LoaderOptions options = new LoaderOptions();
        options.setAllowDuplicateKeys(false);
        options.setMaxAliasesForCollections(50);
        return new Yaml(new SafeConstructor(options));
    }

    @SuppressWarnings("unchecked")
    private static TestConfig parse(Object root, String source) {
        if (!(root instanceof Map)) {
            throw new IllegalArgumentException("YAML root in " + source + " must be a map");
        }
        Map<String, Object> map = (Map<String, Object>) root;

        String name = requireString(map, "name", source);
        String description = optionalString(map, "description");

        Map<String, Object> dbMap = requireMap(map, "database", source);
        TestConfig.Database database = new TestConfig.Database(
                requireInt(dbMap, "port", source),
                requireString(dbMap, "database", source),
                requireString(dbMap, "tableTemplate", source),
                requireString(dbMap, "user", source));

        Map<String, Object> jdbcMap = requireMap(map, "jdbc", source);
        List<String> wrapperPlugins = (List<String>) jdbcMap.get("wrapperPlugins");
        TestConfig.Jdbc jdbc = new TestConfig.Jdbc(
                wrapperPlugins,
                optionalInt(jdbcMap, "bgHighMs"),
                optionalInt(jdbcMap, "connectTimeout"),
                optionalInt(jdbcMap, "socketTimeout"),
                optionalInt(jdbcMap, "failureDetectionTime"),
                optionalInt(jdbcMap, "failureDetectionInterval"),
                optionalInt(jdbcMap, "failureDetectionCount"),
                optionalString(jdbcMap, "wrapperLoggerLevel"));

        Map<String, Object> hikariMap = requireMap(map, "hikari", source);
        TestConfig.Hikari hikari = new TestConfig.Hikari(
                requireInt(hikariMap, "maximumPoolSize", source),
                requireInt(hikariMap, "minimumIdle", source),
                requireLong(hikariMap, "initializationFailTimeout", source),
                requireInt(hikariMap, "connectionTimeoutMs", source),
                requireInt(hikariMap, "idleTimeoutMs", source),
                requireInt(hikariMap, "maxLifetimeMs", source),
                requireInt(hikariMap, "keepaliveTimeMs", source),
                requireInt(hikariMap, "validationTimeoutMs", source),
                optionalString(hikariMap, "connectionInitSql"),
                optionalString(hikariMap, "connectionTestQuery"),
                optionalString(hikariMap, "exceptionOverrideClassName"));

        Map<String, Object> wlMap = requireMap(map, "workload", source);
        Map<String, Object> weights = requireMap(wlMap, "weights", source);
        Map<String, Object> retry = (Map<String, Object>) wlMap.getOrDefault("retry", Map.of());
        TestConfig.Workload workload = new TestConfig.Workload(
                requireInt(wlMap, "threads", source),
                requireInt(wlMap, "intervalMs", source),
                requireInt(weights, "read", source),
                requireInt(weights, "insert", source),
                requireInt(weights, "update", source),
                Boolean.TRUE.equals(retry.get("enabled")),
                ((Number) retry.getOrDefault("delayMs", 0)).intValue());

        return new TestConfig(name, description, database, jdbc, hikari, workload);
    }

    // ---- helpers --------------------------------------------------------

    private static String requireString(Map<String, Object> m, String key, String src) {
        Object v = m.get(key);
        if (v == null) throw missing(key, src);
        return v.toString();
    }

    private static String optionalString(Map<String, Object> m, String key) {
        Object v = m.get(key);
        return v == null ? null : v.toString();
    }

    private static int requireInt(Map<String, Object> m, String key, String src) {
        Object v = m.get(key);
        if (v == null) throw missing(key, src);
        if (v instanceof Number n) return n.intValue();
        throw new IllegalArgumentException(key + " in " + src + " must be a number, got: " + v);
    }

    private static long requireLong(Map<String, Object> m, String key, String src) {
        Object v = m.get(key);
        if (v == null) throw missing(key, src);
        if (v instanceof Number n) return n.longValue();
        throw new IllegalArgumentException(key + " in " + src + " must be a number, got: " + v);
    }

    private static Integer optionalInt(Map<String, Object> m, String key) {
        Object v = m.get(key);
        if (v == null) return null;
        if (v instanceof Number n) return n.intValue();
        throw new IllegalArgumentException(key + " must be a number or null, got: " + v);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> requireMap(Map<String, Object> m, String key, String src) {
        Object v = m.get(key);
        if (!(v instanceof Map)) throw new IllegalArgumentException(key + " in " + src + " must be a map");
        return (Map<String, Object>) v;
    }

    private static IllegalArgumentException missing(String key, String src) {
        return new IllegalArgumentException("required key '" + key + "' missing in " + src);
    }
}
