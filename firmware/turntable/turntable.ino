// FS90R timed drive. Upload with the Arduino Servo library installed.
// Set these two constants for your wiring and measured neutral trim.
#include <Servo.h>
#include <stdio.h>
#include <string.h>

const uint8_t SERVO_PIN = 9;
const int STARTUP_NEUTRAL_US = 1450;
const unsigned long MAX_DRIVE_MS = 500;
Servo motor;
char line[96];
size_t lineLength = 0;
bool discarding = false;
bool driving = false;
unsigned long driveStart = 0;
unsigned long driveDuration = 0;
int neutralUs = STARTUP_NEUTRAL_US;
char activeToken[17] = "";

void reply(const char* kind, const char* token, const char* value) {
  Serial.print(kind); Serial.print(' ');
  Serial.print(token); Serial.print(' ');
  Serial.println(value);
}

void stopMotor() {
  motor.writeMicroseconds(neutralUs);
  driving = false;
}

bool validPulse(int value) { return value >= 1000 && value <= 2000; }

void handleLine() {
  char command[9], token[17], extra;
  int pulse = 0, duration = 0, neutral = 0;
  if (sscanf(line, "%8s %16s", command, token) != 2) {
    stopMotor();
    return;
  }
  if (strcmp(command, "PING") == 0 &&
      sscanf(line, "%8s %16s %c", command, token, &extra) == 2) {
    reply("OK", token, "PING");
  } else if (strcmp(command, "INFO") == 0 &&
             sscanf(line, "%8s %16s %c", command, token, &extra) == 2) {
    char details[40];
    snprintf(details, sizeof(details), "INFO 2 %d %d %lu", STARTUP_NEUTRAL_US,
             (int)SERVO_PIN, MAX_DRIVE_MS);
    reply("OK", token, details);
  } else if (strcmp(command, "STOP") == 0 &&
             sscanf(line, "%8s %16s %d %c", command, token, &neutral, &extra) == 3 &&
             validPulse(neutral)) {
    neutralUs = neutral;
    stopMotor();
    reply("OK", token, "STOP");
  } else if (strcmp(command, "DRIVE") == 0 &&
             sscanf(line, "%8s %16s %d %d %d %c", command, token,
                    &pulse, &duration, &neutral, &extra) == 5 &&
             validPulse(pulse) && validPulse(neutral) &&
             duration > 0 && duration <= (int)MAX_DRIVE_MS) {
    if (driving) {
      reply("ERR", token, "BUSY");
      return;
    }
    neutralUs = neutral;
    strcpy(activeToken, token);
    driveDuration = duration;
    driveStart = millis();
    motor.writeMicroseconds(pulse);
    driving = true;
  } else {
    stopMotor();
    reply("ERR", token, "BAD_COMMAND");
  }
}

void setup() {
  motor.writeMicroseconds(STARTUP_NEUTRAL_US);
  motor.attach(SERVO_PIN, 1000, 2000);
  stopMotor();
  Serial.begin(115200);
  Serial.println("READY AIDA_TURNTABLE_V2");
}

void loop() {
  if (driving && (unsigned long)(millis() - driveStart) >= driveDuration) {
    stopMotor();
    reply("OK", activeToken, "DONE");
  }
  // Limit work per pass so serial traffic cannot starve the motor timer.
  for (uint8_t count = 0; count < 16 && Serial.available(); ++count) {
    char c = Serial.read();
    if (c == '\n') {
      if (!discarding) {
        line[lineLength] = '\0';
        handleLine();
      }
      lineLength = 0;
      discarding = false;
    } else if (c != '\r' && !discarding) {
      if (lineLength < sizeof(line) - 1) line[lineLength++] = c;
      else { stopMotor(); discarding = true; }
    }
  }
}
