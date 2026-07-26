"""Static dashboard rendering."""

from smc_ta.dashboard.performance import (
    PerformanceAnalyticsDashboardConfig,
    PerformanceAnalyticsData,
    load_performance_analytics_data,
    render_performance_analytics_dashboard,
    write_performance_analytics_dashboard,
)
from smc_ta.dashboard.static import render_dashboard_html, render_live_dashboard_html, write_dashboard, write_live_dashboard

__all__ = [
    "PerformanceAnalyticsDashboardConfig",
    "PerformanceAnalyticsData",
    "load_performance_analytics_data",
    "render_dashboard_html",
    "render_live_dashboard_html",
    "render_performance_analytics_dashboard",
    "write_dashboard",
    "write_live_dashboard",
    "write_performance_analytics_dashboard",
]
