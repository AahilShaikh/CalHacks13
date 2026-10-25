// Enumerate in the same AVFoundation order used by OpenCV. No capture session.
#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>

int main(void) {
  @autoreleasepool {
    NSMutableArray *result = [NSMutableArray array];
    NSArray<AVCaptureDevice *> *devices = [AVCaptureDevice devicesWithMediaType:AVMediaTypeVideo];
    [devices enumerateObjectsUsingBlock:^(AVCaptureDevice *device, NSUInteger index, BOOL *stop) {
      NSString *type = device.deviceType;
      BOOL builtin = [type containsString:@"BuiltIn"] || [type containsString:@"Continuity"] ||
                     [type containsString:@"DeskView"];
      BOOL usb = device.transportType == 0x75736220 && !builtin; // FourCC 'usb '
      [result addObject:@{@"index": @(index), @"device_id": device.uniqueID,
                          @"name": device.localizedName, @"is_usb": @(usb)}];
    }];
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
    fwrite(json.bytes, 1, json.length, stdout);
  }
  return 0;
}
