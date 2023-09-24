import { ReactWidget } from '@jupyterlab/apputils';
import Clouder from '../Clouder';

export class ClouderWidget extends ReactWidget {
  constructor() {
    super();
    this.addClass('dla-Clouder');
  }

  render(): JSX.Element {
    return <Clouder />;
  }
}
