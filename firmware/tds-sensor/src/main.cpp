#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

float readTds(int *outRawAdc, int *outMv) {
  long rawSum = 0;
  long mVSum = 0;

  for (int i = 0; i < TDS_SAMPLES; i++) {
    rawSum += analogRead(TDS_PIN);
    mVSum += analogReadMilliVolts(TDS_PIN);
    delay(10);
  }

  int avgRaw = rawSum / TDS_SAMPLES;
  int avgMv = mVSum / TDS_SAMPLES;

  *outRawAdc = avgRaw;
  *outMv = avgMv;

  float voltage = avgRaw * VREF / ((1 << ADC_RESOLUTION) - 1);

  float tempCoefficient = 1.0 + 0.02 * (25.0 - 25.0);
  float compensatedVoltage = voltage / tempCoefficient;

  float tds = (133.42 * compensatedVoltage * compensatedVoltage * compensatedVoltage
               - 255.86 * compensatedVoltage * compensatedVoltage
               + 857.39 * compensatedVoltage)
              * TDS_K_VALUE;

  if (tds < 0) tds = 0;
  return tds;
}

bool connectWiFiWithTimeout() {
  Serial.print("WiFi connecting");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  // ESP32-S3 深度休眠唤醒后，关闭 WiFi modem 休眠可显著提升连接稳定性
  WiFi.setSleep(false);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_TIMEOUT_SEC * 1000UL) {
      Serial.println(" timeout");
      return false;
    }
    delay(500);
    Serial.print(".");
  }
  Serial.print(" OK IP=");
  Serial.println(WiFi.localIP());
  return true;
}

bool publishHttpTo(const char *endpoint, float tds, int rawAdc, int mV) {
  HTTPClient http;
  if (!http.begin(endpoint)) {
    Serial.println("HTTP begin failed");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", "Bearer " API_TOKEN);

  JsonDocument doc;
  doc["sensor_id"] = SENSOR_ID;
  doc["location"] = SENSOR_LOCATION;
  doc["metric"] = SENSOR_METRIC;
  doc["value"] = round(tds * 10) / 10.0;
  doc["unit"] = SENSOR_UNIT;
  doc["name"] = "TDS 水质";
  doc["raw_adc"] = rawAdc;
  doc["raw_mv"] = mV;
  doc["chip_temp"] = round(temperatureRead() * 10) / 10.0;

  char payload[256];
  serializeJson(doc, payload);

  http.setTimeout(HTTP_TIMEOUT_SEC * 1000);
  int code = http.POST(payload);
  bool ok = (code == 200 || code == 201 || code == 204);
  Serial.printf("HTTP POST %s -> %d %s\n", endpoint, code, ok ? "OK" : "FAIL");
  http.end();
  return ok;
}

bool publishHttp(float tds, int rawAdc, int mV) {
  // 优先走公网域名；失败则回落到局域网地址（同一服务器，规避路由器NAT回环限制）
  if (publishHttpTo(API_ENDPOINT, tds, rawAdc, mV)) return true;
  if (publishHttpTo(API_ENDPOINT_LAN, tds, rawAdc, mV)) return true;
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(100);

  // 注意: 不能用 setCpuFrequencyMhz(80) 降频——ESP32-S3 在 80MHz 下 ADC 读数严重失真
  // (实测同一针脚 240MHz 读 957, 80MHz 只读 17)。省电交给深度睡眠即可。
  Serial.printf("CPU: %lu MHz\n", getCpuFrequencyMhz());

  // Init ADC — allow time for analog circuits to stabilize after wake
  analogReadResolution(ADC_RESOLUTION);
  analogSetAttenuation(ADC_11db);
  delay(500);
  // Discard first readings (ADC needs warm-up)
  for (int i = 0; i < 10; i++) {
    analogRead(TDS_PIN);
    analogReadMilliVolts(TDS_PIN);
    delay(10);
  }

  // Read sensor
  int rawAdc, mV;
  float tds = readTds(&rawAdc, &mV);

  Serial.print("TDS: ");
  Serial.print(tds);
  Serial.print(" ppm (ADC=");
  Serial.print(rawAdc);
  Serial.print(" mV=");
  Serial.print(mV);
  Serial.print(")  CPU temp: ");
  Serial.print(temperatureRead());
  Serial.println("C");

  // Connect & publish over HTTP
  if (connectWiFiWithTimeout()) {
    publishHttp(tds, rawAdc, mV);
    WiFi.disconnect(true);
  } else {
    // WiFi 连接失败：软重启重试一次（部分冷启动问题软重启后可恢复）
    Serial.println("WiFi failed — soft restart retry");
    Serial.flush();
    delay(500);
    ESP.restart();
    return;
  }

  Serial.printf("Deep sleep %ds...\n", SLEEP_INTERVAL_SEC);
  Serial.flush();

  esp_sleep_enable_timer_wakeup((uint64_t)SLEEP_INTERVAL_SEC * 1000000);
  esp_deep_sleep_start();
}

void loop() {
  // Never reached — deep sleep restarts from setup()
}
