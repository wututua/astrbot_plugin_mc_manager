package me.lucko.spark.api.statistic;

public interface StatisticWindow {
    enum TicksPerSecond implements StatisticWindow { SECONDS_5, SECONDS_10, MINUTES_1, MINUTES_5, MINUTES_15 }
    enum MillisPerTick implements StatisticWindow { SECONDS_10, MINUTES_1, MINUTES_5 }
    enum CpuUsage implements StatisticWindow { SECONDS_10, MINUTES_1, MINUTES_15 }
}
