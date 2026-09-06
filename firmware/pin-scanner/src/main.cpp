#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

const int adcPins[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20};
const int pinCount = sizeof(adcPins) / sizeof(adcPins[0]);

bool connectWiFiWithTimeout() {
  Serial.print("WiFi connecting");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

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

void publishHttp(int *raws, int maxPin, int maxRaw, float maxVoltage) {
  HTTPClient http;
  if (!http.begin(API_ENDPOINT)) {
    Serial.println("HTTP begin failed");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", "Bearer " API_TOKEN);

  JsonDocument doc;
  doc["sensor_id"] = SENSOR_ID;
  doc["location"] = SENSOR_LOCATION;
  doc["metric"] = SENSOR_METRIC;
  doc["value"] = maxRaw;
  doc["unit"] = SENSOR_UNIT;
  doc["name"] = "GPIO 扫描";
  doc["max_pin"] = maxPin;
  doc["max_voltage"] = maxVoltage;

  JsonObject pins = doc["pins"].to<JsonObject>();
  for (int i = 0; i < pinCount; i++) {
    pins[String(adcPins[i])] = raws[i];
  }

  char payload[512];
  serializeJson(doc, payload);

  http.setTimeout(HTTP_TIMEOUT_SEC * 1000);
  int code = http.POST(payload);
  bool ok = (code == 200 || code == 201 || code == 204);
  Serial.printf("HTTP POST -> %d %s\n", code, ok ? "OK" : "FAIL");
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("Scanner starting");

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  if (!connectWiFiWithTimeout()) {
    Serial.println("WiFi failed, restarting...");
    delay(5000);
    ESP.restart();
  }
  Serial.println("WiFi connected");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    if (!connectWiFiWithTimeout()) {
      delay(5000);
      ESP.restart();
    }
  }

  int raws[pinCount];
  int maxRaw = -1;
  int maxPin = -1;
  float maxVoltage = 0;

  for (int i = 0; i < pinCount; i++) {
    int pin = adcPins[i];
    int raw = analogRead(pin);
    float voltage = raw * 3.3 / 4095.0;
    raws[i] = raw;

    Serial.printf("gpio=%d raw=%d v=%.3f\n", pin, raw, voltage);

    if (raw > maxRaw) {
      maxRaw = raw;
      maxPin = pin;
      maxVoltage = voltage;
    }
    delay(100);
  }

  Serial.printf("MAX gpio=%d raw=%d v=%.3f\n", maxPin, maxRaw, maxVoltage);
  publishHttp(raws, maxPin, maxRaw, maxVoltage);

  delay(3000);
}
