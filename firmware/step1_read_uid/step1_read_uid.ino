// STEP 1: prove the reader works. Prints each card's UID to the Serial Monitor.
// Library needed (Arduino Library Manager): "MFRC522" by GithubCommunity
//
// Wiring (MFRC522 -> ESP32 DevKit):
//   3.3V -> 3V3   (NOT 5V!)     GND -> GND
//   SDA(SS) -> GPIO 5           RST -> GPIO 22
//   SCK -> GPIO 18              MISO -> GPIO 19        MOSI -> GPIO 23
#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN  5
#define RST_PIN 22
MFRC522 rfid(SS_PIN, RST_PIN);

void setup() {
  Serial.begin(115200);
  SPI.begin(18, 19, 23, SS_PIN);   // SCK, MISO, MOSI, SS
  rfid.PCD_Init();
  delay(50);
  Serial.println("MFRC522 firmware check (0x91 or 0x92 means wiring is OK):");
  rfid.PCD_DumpVersionToSerial();
  Serial.println("Tap a card...");
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();

  Serial.print("UID: ");
  Serial.print(uid);
  Serial.print("   type: ");
  Serial.println(rfid.PICC_GetTypeName(rfid.PICC_GetType(rfid.uid.sak)));

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  delay(1000);   // avoid reading the same tap many times
}
