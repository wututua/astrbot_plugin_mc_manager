package astrbot.spark;

import java.lang.instrument.Instrumentation;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.Instant;
import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

/** Read the public spark API inside the server JVM and publish a local report. */
public final class SparkAgent {
    private static Instrumentation instrumentation;
    private static Class<?> provider;
    private static Path output;
    private static boolean reportedReady;

    public static void premain(String arguments, Instrumentation instance) {
        instrumentation = instance;
        output = Paths.get(arguments == null || arguments.trim().isEmpty()
            ? ".astrbot-spark-tps.txt" : arguments.trim()).toAbsolutePath().normalize();
        ScheduledExecutorService timer = Executors.newSingleThreadScheduledExecutor(new ThreadFactory() {
            @Override public Thread newThread(Runnable task) {
                Thread thread = new Thread(task, "astrbot-spark-metrics");
                thread.setDaemon(true);
                return thread;
            }
        });
        timer.scheduleWithFixedDelay(new Runnable() {
            @Override public void run() { sample(); }
        }, 1, 2, TimeUnit.SECONDS);
        System.out.println("[AstrBotSparkAgent] Local spark report: " + output);
    }

    private static Object spark() throws Exception {
        if (provider != null) {
            try { return provider.getMethod("get").invoke(null); }
            catch (Exception ignored) { provider = null; }
        }
        for (Class<?> candidate : instrumentation.getAllLoadedClasses()) {
            if (!candidate.getName().equals("me.lucko.spark.api.SparkProvider")) continue;
            try {
                Object api = candidate.getMethod("get").invoke(null);
                if (api != null) { provider = candidate; return api; }
            } catch (Exception ignored) { /* Another classloader may own an uninitialized copy. */ }
        }
        throw new IllegalStateException("spark API is not ready; ensure spark is installed and enabled");
    }

    private static Method publicInterfaceMethod(Class<?> type, String name, Object[] arguments) {
        for (Class<?> iface : type.getInterfaces()) {
            for (Method method : iface.getMethods()) {
                if (Modifier.isPublic(iface.getModifiers()) && matches(method, name, arguments)) return method;
            }
            Method nested = publicInterfaceMethod(iface, name, arguments);
            if (nested != null) return nested;
        }
        Class<?> parent = type.getSuperclass();
        return parent == null ? null : publicInterfaceMethod(parent, name, arguments);
    }

    private static boolean matches(Method method, String name, Object[] arguments) {
        if (!method.getName().equals(name) || method.getParameterTypes().length != arguments.length) return false;
        Class<?>[] parameters = method.getParameterTypes();
        for (int index = 0; index < arguments.length; index++) {
            if (arguments[index] != null && !parameters[index].isInstance(arguments[index])) return false;
        }
        return true;
    }

    private static Object invoke(Object target, String name, Object... arguments) throws Exception {
        if (target == null) return null;
        Method selected = publicInterfaceMethod(target.getClass(), name, arguments);
        if (selected == null) {
            for (Method candidate : target.getClass().getMethods()) {
                if (matches(candidate, name, arguments)) { selected = candidate; break; }
            }
        }
        if (selected == null) throw new NoSuchMethodException(name);
        try { return selected.invoke(target, arguments); }
        catch (IllegalAccessException inaccessible) {
            selected.setAccessible(true);
            return selected.invoke(target, arguments);
        }
    }

    private static Object window(Object api, String type, String name) throws Exception {
        ClassLoader loader = provider.getClassLoader();
        Class<?> enumeration = Class.forName("me.lucko.spark.api.statistic.StatisticWindow$" + type, true, loader);
        for (Object constant : enumeration.getEnumConstants()) {
            if (((Enum<?>) constant).name().equals(name)) return constant;
        }
        throw new IllegalArgumentException("Unsupported spark window: " + name);
    }

    private static String number(Object value, int precision, double multiplier) {
        if (!(value instanceof Number)) return "N/A";
        double measurement = ((Number) value).doubleValue() * multiplier;
        if (Double.isNaN(measurement) || Double.isInfinite(measurement) || measurement < 0) return "N/A";
        return String.format(Locale.ROOT, "% ." + precision + "f", measurement).trim();
    }

    private static String statistic(Object api, Object statistic, String type, String name, int precision, double multiplier) throws Exception {
        return number(invoke(statistic, "poll", window(api, type, name)), precision, multiplier);
    }

    private static String duration(Object api, Object statistic, String name) throws Exception {
        Object average = invoke(statistic, "poll", window(api, "MillisPerTick", name));
        return number(invoke(average, "min"), 1, 1) + "/"
            + number(invoke(average, "median"), 1, 1) + "/"
            + number(invoke(average, "percentile95th"), 1, 1) + "/"
            + number(invoke(average, "max"), 1, 1);
    }

    private static String cpu(Object api, String method) throws Exception {
        Object statistic = invoke(api, method);
        return statistic(api, statistic, "CpuUsage", "SECONDS_10", 0, 100) + "%, "
            + statistic(api, statistic, "CpuUsage", "MINUTES_1", 0, 100) + "%, "
            + statistic(api, statistic, "CpuUsage", "MINUTES_15", 0, 100) + "%";
    }

    private static String report(Object api) throws Exception {
        StringBuilder text = new StringBuilder("# Spark API sample at " + Instant.now() + "\n");
        Object tps = invoke(api, "tps");
        if (tps != null) {
            text.append("TPS from last 5s, 10s, 1m, 5m, 15m:\n");
            text.append(statistic(api, tps, "TicksPerSecond", "SECONDS_5", 2, 1)).append(", ")
                .append(statistic(api, tps, "TicksPerSecond", "SECONDS_10", 2, 1)).append(", ")
                .append(statistic(api, tps, "TicksPerSecond", "MINUTES_1", 2, 1)).append(", ")
                .append(statistic(api, tps, "TicksPerSecond", "MINUTES_5", 2, 1)).append(", ")
                .append(statistic(api, tps, "TicksPerSecond", "MINUTES_15", 2, 1)).append("\n\n");
        }
        Object mspt = invoke(api, "mspt");
        if (mspt != null) {
            text.append("Tick durations (min/med/95%ile/max ms) from last 10s, 1m:\n")
                .append(duration(api, mspt, "SECONDS_10")).append("; ")
                .append(duration(api, mspt, "MINUTES_1")).append("\n\n");
        }
        text.append("CPU usage from last 10s, 1m, 15m:\n")
            .append(cpu(api, "cpuSystem")).append("  (system)\n")
            .append(cpu(api, "cpuProcess")).append("  (process)\n");
        return text.toString();
    }

    private static void write(String report) throws Exception {
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent)) throw new IllegalStateException("Report directory does not exist");
        Path temporary = Files.createTempFile(parent, ".astrbot-spark-", ".tmp");
        try {
            Files.write(temporary, report.getBytes(StandardCharsets.UTF_8));
            try { Files.setPosixFilePermissions(temporary, PosixFilePermissions.fromString("rw-r--r--")); }
            catch (UnsupportedOperationException ignored) { /* Windows uses inherited ACLs. */ }
            try { Files.move(temporary, output, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING); }
            catch (AtomicMoveNotSupportedException ignored) { Files.move(temporary, output, StandardCopyOption.REPLACE_EXISTING); }
        } finally { Files.deleteIfExists(temporary); }
    }

    private static void sample() {
        try {
            write(report(spark()));
            if (!reportedReady) {
                reportedReady = true;
                System.out.println("[AstrBotSparkAgent] spark API ready; metrics are sampled every 2 seconds");
            }
        } catch (Throwable error) {
            reportedReady = false;
            Throwable cause = error instanceof InvocationTargetException && error.getCause() != null ? error.getCause() : error;
            try { write("Spark API unavailable: " + cause.getClass().getSimpleName() + ": " + cause.getMessage() + "\n"); }
            catch (Exception ignored) { /* A failed write ages out; the bot rejects stale snapshots. */ }
        }
    }

    private SparkAgent() { }
}
