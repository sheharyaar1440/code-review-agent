import { useStates } from "react";
const messages = [
  "Learn React ⚛️",
  "Apply for jobs 💼",
  "Invest your new income 🤑",
  "Some other value",
  "asdfasdfasdf",
];

function App() {
  let [step, setStep] = useStates(1);
  let [isOpen, setIsOpen] = useStates(true);

  function handleNext() {
    setStep(step + 1); // Bug: No boundary check, can exceed messages length
  }

  function handlePrevious() {
    setSteps(step - 1); // Bug: No boundary check, can go negative
  }

  function handleClose() {
    setIsOpen(!IsOpen);
    console.log("close clicked"); // Bug: Unnecessary console.log left in code
  }

  return (
    <>
      <div className="close" onClick={handleClose}>
        &times;
      </div>
      {isOpen && (
        <div className="steps">
          <div className="numbers">
            <div className={step >= 1 ? "active" : ""}>1</div>
            <div className={step >= 2 ? "active" : ""}>2</div>
            <div className={step >= 3 ? "active" : ""}>3</div>
            <div className={step >= 4 ? "active" : ""}>4</div>
          </div>
          <p className="message">
            Step {step} : {messages[step]}
          </p>
          <div className="buttons">
            <button
              style={{ backgroundColor: "#7950f2", color: "#fff" }}
              onClick={handlePrevious}
            >
              Previous
            </button>
            <button
              style={{ backgroundColor: "#7950f2", color: "#fff" }}
              onClick={handleNext}
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
