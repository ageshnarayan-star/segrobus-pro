// Segro Bus scan node (ESP32 + MFRC522) talking HTTPS to the Vercel deployment.
// Libraries (Library Manager): "MFRC522" (GithubCommunity), "ArduinoJson" (v7.x)
// Setup: copy secrets.h.example to secrets.h and fill it in.
//
// Wiring: MFRC522 3.3V->3V3 (NOT 5V), GND->GND, SDA->GPIO5, RST->GPIO22, SCK->GPIO18, MISO->GPIO19, MOSI->GPIO23
//   Green LED: GPIO25 -> 220R -> LED long leg, short leg -> GND
//   Red LED:   GPIO26 -> 220R -> LED long leg, short leg -> GND
//   Active buzzer: + -> GPIO27, - -> GND
// Signals: OK = green + 1 beep. ENROLLED = green + 2 beeps. INVALID 1 red beep, WRONG_BUS/DUPLICATE 2, FULL 3,
//          NETWORK/server problem = red held long.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <MFRC522.h>
#include <ArduinoJson.h>
#include "secrets.h"

#define SS_PIN  5
#define RST_PIN 22
#define GREEN   25
#define RED     26
#define BUZZER  27

MFRC522 rfid(SS_PIN, RST_PIN);

void beep(int ms) { digitalWrite(BUZZER, HIGH); delay(ms); digitalWrite(BUZZER, LOW); }
void beeps(int n, int pin) {
  digitalWrite(pin, HIGH);
  for (int i = 0; i < n; i++) { beep(150); delay(120); }
  delay(500);
  digitalWrite(pin, LOW);
}

void signalResult(const String& r) {
  if (r == "OK") beeps(1, GREEN);
  else if (r == "ENROLLED") beeps(2, GREEN);
  else if (r == "FULL") beeps(3, RED);
  else if (r == "WRONG_BUS" || r == "DUPLICATE") beeps(2, RED);
  else if (r == "INVALID_ID" || r == "UNKNOWN_BUS" || r == "CARD_IN_USE") beeps(1, RED);
  else { digitalWrite(RED, HIGH); beep(800); delay(400); digitalWrite(RED, LOW); }  // NETWORK / BAD_DEVICE_KEY / ERROR
}

bool connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Wi-Fi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    digitalWrite(RED, !digitalRead(RED)); delay(300); Serial.print(".");
  }
  digitalWrite(RED, LOW);
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " failed");
  return WiFi.status() == WL_CONNECTED;
}

// method: "GET" for /api/ping, "POST" for /api/scan. Returns the "result" field or NETWORK.
String call(const char* path, const String& body, bool post) {
  if (!connectWifi()) return "NETWORK";
  WiFiClientSecure client;
  client.setInsecure();  // PROTOTYPE: skips certificate check. For production pin the root CA with client.setCACert().
  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(client, String("https://") + SERVER_HOST + path)) return "NETWORK";
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code;
  if (post) { http.addHeader("Content-Type", "application/json"); code = http.POST(body); }
  else code = http.GET();
  String result = "NETWORK";
  if (code > 0) {
    String payload = http.getString();
    Serial.printf("HTTP %d: %s\n", code, payload.c_str());
    JsonDocument resp;
    if (!deserializeJson(resp, payload)) result = resp["result"] | "ERROR";
  } else {
    Serial.printf("HTTP error: %s\n", http.errorToString(code).c_str());
  }
  http.end();
  return result;
}

String sendScan(const String& uid) {
  JsonDocument doc;
  doc["bus"] = BUS_NUMBER;
  doc["uid"] = uid;
  if (strlen(SHIFT) > 0) doc["shift"] = SHIFT;
  String body;
  serializeJson(doc, body);
  String r = call("/api/scan", body, true);
  if (r == "NETWORK") { delay(500); r = call("/api/scan", body, true); }  // one retry; duplicates are blocked server-side
  return r;
}

void setup() {
  Serial.begin(115200);
  pinMode(GREEN, OUTPUT); pinMode(RED, OUTPUT); pinMode(BUZZER, OUTPUT);
  SPI.begin(18, 19, 23, SS_PIN);
  rfid.PCD_Init();
  Serial.println("Checking server...");
  String r = call("/api/ping", "", false);   // OK = Wi-Fi, HTTPS and device key all work
  Serial.println("Ping: " + r);
  signalResult(r);
  Serial.println("Segro node ready. Tap a card.");
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  Serial.println("Card: " + uid);
  String result = sendScan(uid);
  Serial.println("Result: " + result);
  signalResult(result);
  delay(1000);
}
