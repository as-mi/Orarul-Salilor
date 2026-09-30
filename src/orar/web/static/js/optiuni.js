// Dropdownul "Opționale și facultative" de pe pagina unei grupe (templates/grupa.html).
// Trei niveluri de bife: "Selectează tot" > materie > activitate. Nivelurile de sus
// bifeaza/debifeaza tot ce e sub ele si arata starea partiala (-). Activitatile unei
// materii se strang, afara de cele alese partial. Panoul se inchide la click in afara
// lui. Fara JS, bifele activitatilor merg oricum si toate sunt la vedere.
for (const d of document.querySelectorAll("details.optiuni")) {
  const toate = d.querySelector("[data-toate]");
  const bife = [...d.querySelectorAll('input[name="vizibile"]')];
  const materii = [...d.querySelectorAll("[data-materie]")].map((el) => ({
    el,
    cap: el.querySelector("[data-grup]"),
    copii: [...el.querySelectorAll('input[name="vizibile"]')],
    numar: el.querySelector(".extinde .numar"),
    buton: el.querySelector(".extinde"),
  }));
  const stare = (cap, lista) => {
    const n = lista.filter((b) => b.checked).length;
    cap.checked = n === lista.length;
    cap.indeterminate = n > 0 && n < lista.length;
    return n;
  };
  const sincron = () => {
    for (const m of materii) m.numar.textContent = `${stare(m.cap, m.copii)}/${m.copii.length}`;
    stare(toate, bife);
  };
  const extinde = (m, deschis) => {
    m.el.classList.toggle("strans", !deschis);
    m.buton.setAttribute("aria-expanded", String(deschis));
  };
  toate.addEventListener("change", () => { bife.forEach((b) => (b.checked = toate.checked)); sincron(); });
  for (const m of materii) {
    m.cap.addEventListener("change", () => { m.copii.forEach((b) => (b.checked = m.cap.checked)); sincron(); });
    m.buton.addEventListener("click", () => extinde(m, m.el.classList.contains("strans")));
    extinde(m, m.el.dataset.partiala === "1");
  }
  bife.forEach((b) => b.addEventListener("change", sincron));
  document.addEventListener("click", (e) => { if (d.open && !d.contains(e.target)) d.open = false; });
  // Langa marginea dreapta, panoul se deschide spre stanga, ca sa ramana pe ecran.
  d.addEventListener("toggle", () => {
    const panou = d.querySelector(".panou-optiuni");
    panou.classList.remove("spre-stanga");
    if (d.open && panou.getBoundingClientRect().right > window.innerWidth - 8) panou.classList.add("spre-stanga");
  });
  sincron();
}
