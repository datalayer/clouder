import { useState, useEffect } from 'react';
import { Box, Text } from '@primer/react';
import { Table, DataTable, PageHeader } from '@primer/react/drafts';
import { requestAPI } from '../../jupyterlab/handler';

type OvhProject = {
  id: number,
  projectId: string;
}

const ProjectsTab = () => {
  const [projects, setProjects] = useState(new Array<OvhProject>());
  useEffect(() => {
    requestAPI<any>('ovh/projects')
    .then(data => {
      const projects = (data.projects as [any]).map((project, id) => {
        return {
          id,
          projectId: project,
        }
      }) as [OvhProject];
      setProjects(projects);
    })
    .catch(reason => {
      console.error(
        `Error while accessing the jupyter server clouder extension.\n${reason}`
      );
    });
  }, []);
  return (
    <>
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title>Projects</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box>
        <Table.Container>
          <Table.Title as="h2" id="projects">
            OVHcloud Projects
          </Table.Title>
          <Table.Subtitle as="p" id="projects-subtitle">
            List of projects.
          </Table.Subtitle>
          <DataTable
            aria-labelledby="projects"
            aria-describedby="projects-subtitle" 
            data={projects}
            columns={[
              {
                header: 'Id',
                field: 'projectId',
                renderCell: row => <Text>{row.projectId}</Text>
              },
            ]}
          />
        </Table.Container>
      </Box>
    </>
  )
}

export default ProjectsTab;
