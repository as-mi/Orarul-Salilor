// Formularele tutoriatelor (templates/tutoriat_formular.html, _tut_*.html).

// "toate ..." bifeaza sau debifeaza tot grupul si tine pasul cu bifele lui.
// Merge pe orice `.bife-grup` din pagina, nu doar pe formularul clasic de tutoriat,
// ca sa se poata folosi si in formularul de posturi al campaniilor.
(() => {
  const bifele = (grup) => [...grup.querySelectorAll('input[type="checkbox"]:not([data-toate])')];
  const tine_pasul = (grup) => {
    const toate = grup.querySelector("[data-toate]");
    if (!toate) return;
    const bifate = bifele(grup).filter((b) => b.checked).length;
    toate.checked = bifate > 0 && bifate === bifele(grup).length;
    toate.indeterminate = bifate > 0 && !toate.checked;
  };
  for (const grup of document.querySelectorAll(".bife-grup")) {
    tine_pasul(grup);
    grup.addEventListener("change", (e) => {
      if (e.target.matches("[data-toate]")) bifele(grup).forEach((b) => { b.checked = e.target.checked; });
      tine_pasul(grup);
    });
  }

  // un tutor bifat ca persoana de contact intra si in echipa (doar in formularul clasic)
  const formular = document.getElementById("formular-tutoriat");
  if (!formular) return;
  formular.addEventListener("change", (e) => {
    if (!e.target.matches('input[name="principal"]')) return;
    const membru = e.target.closest(".tut-membru");
    const bifa = membru && membru.querySelector('input[name="tutori"]');
    if (bifa) bifa.checked = true;
  });
})();
