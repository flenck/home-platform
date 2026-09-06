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
#define SENSOR_ID "esp32-pin-scanner"
#define SENSOR_LOCATION "lab"
#define SENSOR_METRIC "adc"
#define SENSOR_UNIT "raw"

// Timeouts (seconds)
#define WIFI_TIMEOUT_SEC 15
#define HTTP_TIMEOUT_SEC 5

#endif
