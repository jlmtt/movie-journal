// หน้า My Journal: ชื่อหนังที่ขึ้นบรรทัดใหม่ (ไม่ได้อยู่บรรทัดเดียวกับวันที่) → ใส่ class "jr-wrapped" ให้หัวบันทึก
// CSS จะซ่อนจุดคั่น "·" หน้าชื่อหนัง  (เช็กใหม่ทุกครั้งที่หน้าเปลี่ยน / ฟอนต์โหลดเสร็จ / ขนาดจอเปลี่ยน)
function markWrappedTitles() {
    document.querySelectorAll(".jr-head").forEach(function (head) {
        var date = head.querySelector(".jr-date");
        var movie = head.querySelector(".jr-movie");
        if (!date || !movie) return;
        head.classList.remove("jr-wrapped");   // วัดตอนมีจุดคั่นอยู่ในบรรทัดเดียวกับวันที่
        if (movie.offsetTop > date.offsetTop + 2) head.classList.add("jr-wrapped");
    });
}

var pending = false;
function scheduleMark() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () { pending = false; markWrappedTitles(); });
}

new MutationObserver(scheduleMark).observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("resize", scheduleMark);
if (document.fonts) document.fonts.addEventListener("loadingdone", scheduleMark);   // ฟอนต์ลายมือโหลดเสร็จ → ความกว้างตัวหนังสือเปลี่ยน
