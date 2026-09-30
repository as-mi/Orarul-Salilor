// Formularul unei activitati (templates/admin_activitate.html).
// Campul pentru o sala noua apare doar cand alegi "altă sală…". Fara JS ramane ascuns
// pana il ceri, iar ce scrii in el are oricum intaietate la salvare.
(() => {
  const alege = document.getElementById("alege-sala");
  const camp = document.getElementById("camp-sala-noua");
  const sincron = () => {
    const alta = alege.value === alege.dataset.alta;
    camp.hidden = !alta;
    if (!alta) camp.querySelector("input").value = "";
  };
  alege.addEventListener("change", () => { sincron(); if (!camp.hidden) camp.querySelector("input").focus(); });
})();
