from .config import MODEL_NAME, ROBOT_FLEET_CONFIG
from .ai_fleet import OllamaFleetManager

# ==========================================
# 🚀 MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    # แจ้งเตือนก่อนเริ่มรัน
    print(f"🚀 กำลังเริ่มต้นระบบด้วย Ollama (Model: {MODEL_NAME})...")
    
    fleet_manager = OllamaFleetManager(MODEL_NAME, ROBOT_FLEET_CONFIG)
    
    print("\n" + "="*50)
    print("💬 ระบบพร้อมใช้งานแล้ว! ลองพิมพ์คำสั่งดูเลยครับ (พิมพ์ 'exit' เพื่อเลิกทำงาน)")
    print("="*50)
    
    while True:
        # 1. เปลี่ยนข้อความ Input ให้ถามผู้ใช้ตรงๆ ว่าจะให้ทำอะไรต่อ
        user_cmd = input("\n👉 พิมพ์คำสั่งให้หุ่นยนต์ทำอะไรต่อดีครับ? : ")
        
        # 2. เช็คคำสั่งออกระบบ
        if user_cmd.lower() in ['exit', 'quit']:
            print("🛑 กำลังปิดระบบและตัดการเชื่อมต่อหุ่นยนต์...")
            for r in fleet_manager.robots.values():
                r.disconnect()
            print("👋 ปิดระบบเรียบร้อยครับ")
            break
            
        # 3. ส่งคำสั่งให้ AI ประมวลผลและหุ่นยนต์ขยับ
        fleet_manager.send_prompt(user_cmd)
        
        # 4. เมื่อฟังก์ชันบรรทัดบนทำงานเสร็จสมบูรณ์ จะปริ้นท์ข้อความนี้เพื่อบอกให้ผู้ใช้รู้
        print("-" * 50)
        
        print("✅ กระบวนการทำงานล่าสุดเสร็จสิ้น ระบบพร้อมรับคำสั่งต่อไปแล้วครับ!")