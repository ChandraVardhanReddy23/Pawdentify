import React from "react";
import paw from "../assets/dogpaw.png";

export default function PawMotif({ className = "h-10 w-10", alt = "" }) {
  return <img src={paw} alt={alt} aria-hidden={!alt} className={`${className} object-contain`} />;
}
