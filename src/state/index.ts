import { makeObservable, observable, action } from "mobx";

export class AppState {
  tab: number;
  constructor(tab: number = 0.0) {
    makeObservable(this, {
      tab: observable,
      setTab: action,
    });
    this.tab = tab;
  }
  setTab(tab: number) {
    this.tab = tab;
  }
}

const store = new AppState();

export default store;
