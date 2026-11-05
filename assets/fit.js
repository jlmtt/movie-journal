// ย่อสมุดให้พอดีหน้าต่างเบราว์เซอร์ (จอใหญ่ = ขนาดปกติ 1.15 เท่า, หน้าต่างเล็ก = ย่อลงจนเห็นครบทั้งเล่ม)
// สมุดกางเต็มที่กว้าง ~1010px (840 + แถบอินเด็กซ์) สูง 580px ก่อนขยาย → ตั้งค่า CSS --book-scale ให้ style.css ใช้
function fitBook() {
    var scale = Math.min(1.15, (window.innerWidth - 40) / 1010, (window.innerHeight - 40) / 600);
    var root = document.documentElement.style;
    root.setProperty("--book-scale", scale.toFixed(3));
    root.setProperty("--book-open-scale", Math.min(1, scale).toFixed(3));   // ตอนปกเปิดในหน้า Login (ก่อนซูม)
}

fitBook();
window.addEventListener("resize", fitBook);
