// English UI with Marathi alongside for key labels (module names, main buttons, headings).

export const T = {
  home: { en: "Home", mr: "मुख्यपृष्ठ" },
  city: { en: "City: fair 10% water cut", mr: "शहर: न्याय्य १०% पाणीकपात" },
  cityShort: { en: "City", mr: "शहर" },
  village: { en: "Village: tanker planner", mr: "गाव: टँकर नियोजन" },
  villageShort: { en: "Village", mr: "गाव" },
  blunt: { en: "Blunt cut", mr: "सरसकट कपात" },
  fair: { en: "Fair cut", mr: "न्याय्य कपात" },
  wards: { en: "Wards", mr: "प्रभाग" },
  ward: { en: "Ward", mr: "प्रभाग" },
  leaks: { en: "Leak zones", mr: "गळती क्षेत्रे" },
  tryPlan: { en: "Try your own plan", mr: "तुमची योजना तपासा" },
  runPlan: { en: "Simulate my plan", mr: "माझी योजना तपासा" },
  tankers: { en: "Number of tankers", mr: "टँकरची संख्या" },
  fillPoints: { en: "Filling points", mr: "भरणा केंद्रे" },
  planTankers: { en: "Plan tankers on AWS", mr: "टँकर नियोजन करा" },
  compare: { en: "First come, first served vs JalNyay", mr: "आधी आलेल्यास आधी विरुद्ध जलन्याय" },
  fleet: { en: "How many tankers are needed?", mr: "किती टँकर लागतील?" },
  fraud: { en: "Suspicious tanker trips", mr: "संशयास्पद टँकर फेऱ्या" },
  villages: { en: "Villages ranked by need", mr: "गरजेनुसार गावांची यादी" },
  countdown: { en: "until the 10% water cut", mr: "पाणीकपात सुरू होण्यास" },
  open: { en: "Open", mr: "उघडा" },
  how: { en: "How it works", mr: "कसे काम करते" },
  rerunFraud: { en: "Re-run fraud check on AWS", mr: "AWS वर पुन्हा तपासा" },
  inForce: { en: "Water cut in force", mr: "पाणीकपात लागू" },
};

export function Bi({ t, as: Tag = "span", className }: { t: { en: string; mr: string }; as?: "span" | "h1" | "h2" | "h3"; className?: string }) {
  return (
    <Tag className={className}>
      {t.en} <span className="mr" lang="mr">{t.mr}</span>
    </Tag>
  );
}
