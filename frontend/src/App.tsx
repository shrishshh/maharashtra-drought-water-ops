import { useEffect, useState } from "react";
import { Bi, T } from "./i18n";
import Home from "./pages/Home";
import City from "./pages/City";
import VillagePage from "./pages/Village";
import How from "./pages/How";

type Route = "home" | "city" | "village" | "how";
const routeFromHash = (): Route => {
  const h = window.location.hash.replace(/^#\/?/, "");
  return h === "city" || h === "village" || h === "how" ? h : "home";
};

const AWS_SERVICES = [
  "AWS Lambda (container images)", "Amazon API Gateway", "Amazon DynamoDB", "Amazon S3",
  "Amazon Location Service", "Amazon ECR", "Amazon CloudWatch", "AWS Amplify Hosting",
];

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);
  useEffect(() => {
    const on = () => {
      setRoute(routeFromHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  return (
    <>
      <nav className="topbar">
        <a href="#/" className="brand">💧 JalNyay <span className="mr" lang="mr">जलन्याय</span></a>
        <div className="navlinks">
          <a href="#/" className={route === "home" ? "on" : ""}><Bi t={T.home} /></a>
          <a href="#/city" className={route === "city" ? "on" : ""}><Bi t={T.cityShort} /></a>
          <a href="#/village" className={route === "village" ? "on" : ""}><Bi t={T.villageShort} /></a>
          <a href="#/how" className={route === "how" ? "on" : ""}><Bi t={T.how} /></a>
        </div>
      </nav>
      <main>{route === "city" ? <City /> : route === "village" ? <VillagePage /> : route === "how" ? <How /> : <Home />}</main>
      <footer className="footer">
        <div>
          <h3>Data sources</h3>
          <ul>
            <li>City: EPA Net3 sample network via WNTR (a representative network, <b>not</b> a real Maharashtra network); wards and leaks SIMULATED.</li>
            <li>Village locations: © OpenStreetMap contributors (ODbL), Tuljapur taluka, Dharashiv.</li>
            <li>Population: Census 2011 village totals (Osmanabad district), via census2011.co.in.</li>
            <li>Road distances: Amazon Location Service (cached for at most 30 days).</li>
            <li>SIMULATED: livestock, source status, tanker requests and history, GPS traces and trip claims.</li>
            <li>Assumptions to verify: water norms (20 L/person/day; livestock 35 / 10 L per day), filling points (Tuljapur, Naldurg).</li>
          </ul>
        </div>
        <div>
          <h3>Built on AWS</h3>
          <ul>{AWS_SERVICES.map((s) => <li key={s}>{s}</li>)}</ul>
          <p>
            <a href="https://github.com/shrishshh/maharashtra-drought-water-ops" target="_blank" rel="noreferrer">Source code on GitHub</a>
            {" · "}WeMakeDevs × AWS "Environmental Hacks", Oct 2026
          </p>
        </div>
      </footer>
    </>
  );
}
