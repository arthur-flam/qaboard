import { Link } from "../router";
import styled from "styled-components";

import { image_url } from "../git";

const AvatarCell = styled.div`
  width: ${props => props.size || '45px'};
  color: rgba(0, 0, 0, 0.85);

  align-self: center;
`;

const AvatarImg = styled.img`
  width: ${props => props.size || '45px'};
  height: ${props => props.size || '45px'};
  margin-right: 10px;
  padding: 0;

  border-radius: 50%;
  border: 1px solid rgba(0,0,0,0.1);
  float: left;
  transition: border-color 100ms linear;

  vertical-align: middle;
  align-self: center;

  filter: ${props => props.filter || null};
`;

const AvatarPlaceholder = styled.div`
  background-color: #E3F2FD;
  color: #555;
  text-decoration: none;

  font-size: 16px;
  line-height: 38px;
  text-align: center;
  vertical-align: center;

  border-radius: 50%;
  border: none;
  height: auto;
  width: ${props => props.size || '45px'};
  height: ${props => props.size || '45px'};
  margin: 0;

  vertical-align: middle;
  align-self: center;
`;

const Avatar = ({ src, href, alt, size, style = {}, img_style = {} }) => {
  const no_image = src === null || src === undefined || src === false;
  const avatar = no_image ? <AvatarPlaceholder size={size} style={style}>{alt?.[0]?.toUpperCase() ?? ''}</AvatarPlaceholder>
                          : <AvatarImg size={size} style={{...style, ...img_style}} alt={alt || ''} src={src || ''} />;
  if (href !== undefined && href !== null && href !== false)
    return <AvatarCell size={size} style={style}><Link to={href || '#'}>{avatar}</Link></AvatarCell>;
  return <AvatarCell size={size} style={style}>{avatar}</AvatarCell>;
}


// Links to the committer's page when we know the project
const CommitAvatar = ({ commit, project, size, style }) => {
  const avatar_url = image_url(commit?.committer_avatar_url)
  const href = (project && commit?.committer_name) ? `/${project}/committer/${commit.committer_name}` : null
  return <Avatar
    href={href}
    alt={commit?.committer_name ?? ''}
    src={avatar_url}
    style={style}
    size={size}
  />
}


export { Avatar, CommitAvatar };
