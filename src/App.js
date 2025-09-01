import { useState } from "react";

const messages = [
  "Learn React ⚛️",
  "Apply for jobs 💼",
  "Invest your new income 🤑",
];
function App() {
  const [step, setStep] = useState(1);
  const [isOpen, setIsOpen] = useState(true);
  function handleNext() {
    if (step < 3) setStep(step + 1);
  }
  function handlePrevious() {
    if (step > 1) setStep(step - 1);
  }
  function handleClose() {
    setIsOpen(!isOpen);
    console.log("ahjdkhfas");
  }

  return (
    <>
      <div className="close" onClick={handleClose}>
        &times;
      </div>
      {isOpen && (
        <div className="steps">
          <div className="numbers">
            <div className={`${step >= 1 ? "active" : ""}`}>1</div>
            <div className={`${step >= 2 ? "active" : ""}`}>2</div>
            <div className={`${step >= 3 ? "active" : ""}`}>3</div>
          </div>
          <p className="message">
            Step {step} : {messages[step - 1]}
          </p>
          <div className="buttons">
            <button
              style={{ backgroundColor: "#7950f2", color: "#fff" }}
              onClick={handlePrevious}
              disabled={step === 1}
            >
              Previous
            </button>
            <button
              style={{ backgroundColor: "#7950f2", color: "#fff" }}
              onClick={handleNext}
              disabled={step === 3}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </>
  );
}

export default App;
