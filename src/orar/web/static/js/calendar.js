// „Copiază linkul” din pagina de ajutor a abonamentului la calendar.
// Clipboard API cere un context sigur (HTTPS sau localhost); pe http simplu cade pe
// selectarea textului, care merge peste tot.
for (const buton of document.querySelectorAll("[data-copiaza]")) {
  buton.addEventListener("click", async () => {
    const camp = document.getElementById(buton.dataset.copiaza);
    if (!camp) return;
    try {
      await navigator.clipboard.writeText(camp.value);
    } catch {
      camp.focus();
      camp.select();
      document.execCommand("copy");
    }
    const text = buton.textContent;
    buton.textContent = "Copiat!";
    setTimeout(() => {
      buton.textContent = text;
    }, 2000);
  });
}
