#ifndef CONFIG_H
#define CONFIG_H

// Wi-Fi credentials
#define WIFI_SSID "ZTE-x3H49Y"
#define WIFI_PASSWORD "123456789"

// 服务器 API（内网穿透公网地址；同一局域网内回落到服务器局域网IP，避免NAT回环问题）
#define API_ENDPOINT "http://myhomegrid.cc.cd:8000/api/v1/readings"
#define API_ENDPOINT_LAN "http://192.168.5.7:8000/api/v1/readings"
#define API_TOKEN "homeplatform"

// Sensor identity
#define SENSOR_ID "esp32-tds-sensor"
#define SENSOR_LOCATION "water"
#define SENSOR_METRIC "tds"
#define SENSOR_UNIT "ppm"

// Sensor
#define TDS_PIN 4
#define ADC_RESOLUTION 12
#define VREF 3.3
#define SLEEP_INTERVAL_SEC 10
#define TDS_SAMPLES 50

// Timeouts (seconds)
#define WIFI_TIMEOUT_SEC 15
#define HTTP_TIMEOUT_SEC 5

// TDS calibration coefficient (sensor-specific)
// 校准记录（重要!）:
//   发现 setCpuFrequencyMhz(80) 降频会严重失真 ADC (960 -> 17)，旧校准全部作废
//   全速(240MHz)真实读数: ADC=951-963, V=0.776V -> 多项式=574
//   水样: 参考 306 ppm -> K = 306/574 = 0.53
//   实测 0.53 平均 302.7 ppm -> 精调 K = 0.53*306/302.7 = 0.536
//   实测 0.536 平均 300.7 ppm -> 精调 K = 0.536*306/300.7 = 0.545
#define TDS_K_VALUE 0.545

#endif
