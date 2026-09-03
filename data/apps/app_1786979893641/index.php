<?php
header("Content-Type: text/html; charset=utf-8");
echo "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>PHP Demo</title>";
echo "<style>body{font-family:system-ui;background:#0b0e14;color:#d7dce8;padding:2rem}code{background:#171c29;padding:2px 6px;border-radius:4px;color:#22d3ee}</style></head><body>";
echo "<h2>🐘 PHP Demo App</h2>";
echo "<p>PHP version: <code>" . PHP_VERSION . "</code></p>";
echo "<p>Server time: <code>" . date("Y-m-d H:i:s") . "</code></p>";
echo "<p>Hosted on <b>VPS-PANEL</b> via <code>php -S</code></p>";
echo "</body></html>";
