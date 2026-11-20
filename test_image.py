import cv2
from core.upscale import build_upscaler
import warnings 
warnings.filterwarnings("ignore")

#1.تحميل النموذج
print("جاري تحميل النموذج...")
upsampler = build_upscaler(scale=2)

# 2. قراءة أول إطار من الفيديو
cap = cv2.VideoCapture("input.mp4")
ret, frame = cap.read()
cap.release()

if ret:
    #حفظ الصوره الأصليه
    cv2.imwrite("original_frame.jpg", frame)

    #3. تحسين الصوره
    print("جاري تحسين الصوره...")
    output, _ = upsampler.enhance(frame, outscale=2)

    #حفظ صوره المحسنه
    cv2.imwrite("enhanced_frame.jpg", output)
    print("✅ تم حفظ الصورتين: original_frame.jpg و enhanced_frame.jpg")
else:
    print("❌ لم يتم العثور على الفيديو!")