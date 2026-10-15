package me.lucko.spark.api;

import me.lucko.spark.api.statistic.StatisticWindow;

/** A deterministic public-API fixture; it is never shipped inside the agent. */
public final class SparkProvider {
    public interface Statistic { Object poll(StatisticWindow window); }
    public interface Average { double min(); double median(); double percentile95th(); double max(); }
    public interface Spark { Statistic tps(); Statistic mspt(); Statistic cpuSystem(); Statistic cpuProcess(); }
    private static final Spark SPARK = new FixtureSpark();
    public static Spark get() { return SPARK; }

    private static final class FixtureSpark implements Spark {
        public Statistic tps() { return new Values("tps"); }
        public Statistic mspt() { return new Values("mspt"); }
        public Statistic cpuSystem() { return new Values("system"); }
        public Statistic cpuProcess() { return new Values("process"); }
    }
    private static final class Values implements Statistic {
        private final String type;
        Values(String type) { this.type = type; }
        public Object poll(StatisticWindow window) {
            String name = ((Enum<?>) window).name();
            if (type.equals("tps")) {
                if (name.equals("SECONDS_5")) return 13.24;
                if (name.equals("SECONDS_10")) return 13.37;
                if (name.equals("MINUTES_1")) return 15.95;
                if (name.equals("MINUTES_5")) return 19.03;
                return 19.67;
            }
            if (type.equals("mspt")) return new Durations(name.equals("SECONDS_10"));
            if (type.equals("system")) return name.equals("SECONDS_10") ? 0.33 : 0.35;
            return name.equals("MINUTES_15") ? 0.15 : 0.14;
        }
    }
    private static final class Durations implements Average {
        private final boolean shortWindow;
        Durations(boolean value) { shortWindow = value; }
        public double min() { return shortWindow ? 69.1 : 31.1; }
        public double median() { return shortWindow ? 74.3 : 64.6; }
        public double percentile95th() { return shortWindow ? 79.6 : 77.0; }
        public double max() { return shortWindow ? 103.2 : 103.5; }
    }
    public static void main(String[] arguments) throws Exception {
        Class.forName("me.lucko.spark.api.SparkProvider");
        Thread.sleep(3800);
    }
}
